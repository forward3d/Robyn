# base_model_executor.py
# pyre-strict

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, List
import numpy as np
from robyn.data.entities.calibration_input import CalibrationInput
from robyn.data.entities.holidays_data import HolidaysData
from robyn.data.entities.hyperparameters import Hyperparameters
from robyn.data.entities.mmmdata import MMMData
from robyn.modeling.entities.enums import Models, NevergradAlgorithm
from robyn.data.entities.enums import AdstockType
from robyn.modeling.entities.modeloutputs import ModelOutputs
from robyn.modeling.entities.modelrun_trials_config import TrialsConfig
from robyn.modeling.feature_engineering import FeaturizedMMMData
from robyn.modeling.transformations.transformations import Transformation
import nevergrad as ng


class BaseModelExecutor(ABC):
    """
    Abstract base class for executing marketing mix models.

    This class defines the interface for model executors and provides common
    initialization logic for different types of models.
    """

    def __init__(
        self,
        mmmdata: MMMData,
        holidays_data: HolidaysData,
        hyperparameters: Hyperparameters,
        calibration_input: CalibrationInput,
        featurized_mmm_data: FeaturizedMMMData,
    ) -> None:
        """
        Initialize the BaseModelExecutor.

        Args:
            mmmdata (MMMData): Marketing Mix Model data.
            holidays_data (HolidaysData): Holiday data for the model.
            hyperparameters (Hyperparameters): Model hyperparameters.
            calibration_input (CalibrationInput): Calibration input data.
            featurized_mmm_data (FeaturizedMMMData): Featurized MMM data.
        """
        self.mmmdata = mmmdata
        self.holidays_data = holidays_data
        self.hyperparameters = hyperparameters
        self.calibration_input = calibration_input
        self.featurized_mmm_data = featurized_mmm_data
        self.transformation = Transformation(mmmdata)

    @abstractmethod
    def model_run(
        self,
        dt_hyper_fixed: Optional[Dict[str, Any]] = None,
        ts_validation: bool = False,
        add_penalty_factor: bool = False,
        refresh: bool = False,
        seed: int = 123,
        cores: Optional[int] = None,
        trials_config: Optional[TrialsConfig] = None,
        rssd_zero_penalty: bool = True,
        objective_weights: Optional[Dict[str, float]] = None,
        nevergrad_algo: NevergradAlgorithm = NevergradAlgorithm.TWO_POINTS_DE,
        intercept: bool = True,
        intercept_sign: str = "non_negative",
        outputs: bool = False,
        model_name: Models = Models.RIDGE,
        lambda_control: Optional[float] = None,
    ) -> ModelOutputs:
        """
        Execute the model run.

        This abstract method should be implemented by subclasses to define the
        specific logic for running the marketing mix model.

        Args:
            dt_hyper_fixed (Optional[Dict[str, Any]]): Fixed hyperparameters.
            ts_validation (bool): Whether to use time series validation.
            add_penalty_factor (bool): Whether to add penalty factors.
            refresh (bool): Whether to refresh the model.
            seed (int): Random seed for reproducibility.
            cores (Optional[int]): Number of CPU cores to use.
            trials_config (Optional[TrialsConfig]): Configuration for trials.
            rssd_zero_penalty (bool): Whether to apply zero penalty in RSSD calculation.
            objective_weights (Optional[Dict[str, float]]): Weights for objectives.
            nevergrad_algo (NevergradAlgorithm): Nevergrad algorithm to use.
            intercept (bool): Whether to include an intercept.
            intercept_sign (str): Sign constraint for the intercept.
            outputs (bool): Whether to generate additional outputs.
            model_name (Models): Name of the model to use.

        Returns:
            ModelOutputs: The outputs of the model run.
        """
        pass

    def _validate_input(self) -> None:
        """
        Validate the input data and parameters.

        Raises:
            ValueError: If any required data or parameter is missing or invalid.
        """
        if self.mmmdata is None:
            raise ValueError("MMMData is required.")
        if self.holidays_data is None:
            raise ValueError("HolidaysData is required.")
        if self.hyperparameters is None:
            raise ValueError("Hyperparameters are required.")
        if self.featurized_mmm_data is None:
            raise ValueError("FeaturizedMMMData is required.")

    def _prepare_hyperparameters(
        self,
        dt_hyper_fixed: Optional[Dict[str, Any]],
        add_penalty_factor: bool,
        ts_validation: bool,
    ) -> Dict[str, Any]:
        prepared_hyperparameters = self.hyperparameters.copy()

        # Update with fixed hyperparameters if provided
        if dt_hyper_fixed:
            for key, value in dt_hyper_fixed.items():
                if prepared_hyperparameters.has_channel(key):
                    channel_params = prepared_hyperparameters.get_hyperparameter(key)
                    for param in ["thetas", "shapes", "scales", "alphas", "gammas", "penalty"]:
                        if param in value:
                            setattr(channel_params, param, value[param])

        # Add penalty factors if required
        if add_penalty_factor:
            for channel in prepared_hyperparameters.hyperparameters:
                channel_params = prepared_hyperparameters.get_hyperparameter(channel)
                channel_params.penalty = [0, 1]  # Example of setting penalty

        # Handle train_size if using time series validation
        if ts_validation and not prepared_hyperparameters.train_size:
            prepared_hyperparameters.train_size = [0.5, 0.8]

        # Extract hyperparameters to be optimized
        hyper_to_optimize = {}
        for channel, channel_params in prepared_hyperparameters.hyperparameters.items():
            for param in ["thetas", "shapes", "scales", "alphas", "gammas"]:
                values = getattr(channel_params, param)
                if isinstance(values, list) and len(values) == 2:
                    hyper_to_optimize[f"{channel}_{param}"] = values

        # Add lambda and train_size if they are to be optimized
        if isinstance(prepared_hyperparameters.lambda_, list) and len(prepared_hyperparameters.lambda_) == 2:
            hyper_to_optimize["lambda"] = prepared_hyperparameters.lambda_
        if isinstance(prepared_hyperparameters.train_size, list) and len(prepared_hyperparameters.train_size) == 2:
            hyper_to_optimize["train_size"] = prepared_hyperparameters.train_size

        return {"prepared_hyperparameters": prepared_hyperparameters, "hyper_to_optimize": hyper_to_optimize}

    def _setup_nevergrad_optimizer(
        self,
        hyperparameters: Dict[str, Any],
        iterations: int,
        cores: int,
        nevergrad_algo: NevergradAlgorithm,
    ) -> ng.optimizers.base.Optimizer:
        hyper_to_optimize = hyperparameters["hyper_to_optimize"]

        if not hyper_to_optimize:
            raise ValueError("No hyperparameters to optimize. Please check your hyperparameter configuration.")

        instrum_dict = {
            name: ng.p.Scalar(lower=bounds[0], upper=bounds[1]) for name, bounds in hyper_to_optimize.items()
        }

        instrum = ng.p.Instrumentation(**instrum_dict)

        return ng.optimizers.registry[nevergrad_algo.value](instrum, budget=iterations, num_workers=cores)

    def _calculate_objective(
        self,
        train_score: float,
        test_score: Optional[float],
        rssd: float,
        objective_weights: Optional[Dict[str, float]],
    ) -> float:
        """
        Calculate the objective function value.

        Args:
            train_score (float): Model's score on the training data.
            test_score (Optional[float]): Model's score on the test data, if available.
            rssd (float): Root Sum Squared Distance.
            objective_weights (Optional[Dict[str, float]]): Weights for objectives.

        Returns:
            float: Calculated objective function value.
        """
        if objective_weights is None:
            objective_weights = {"train_score": 1.0, "test_score": 1.0, "rssd": 1.0}

        objective = (
            objective_weights["train_score"] * (1 - train_score)
            + objective_weights.get("test_score", 0) * (1 - (test_score or 0))
            + objective_weights["rssd"] * rssd
        )

        return objective

    def _apply_transformations(self, channel: str, hyperparameters: Dict[str, Any]) -> np.ndarray:
        """
        Apply adstock and saturation transformations to a channel.

        Args:
            channel (str): Name of the media channel.
            hyperparameters (Dict[str, Any]): Hyperparameters for the transformations.

        Returns:
            np.ndarray: Transformed channel data.
        """
        # Apply adstock transformation
        adstock_type = self.mmmdata.adstock
        if adstock_type == AdstockType.GEOMETRIC:
            adstock_result = self.transformation.adstock_geometric(channel, hyperparameters[f"{channel}_theta"])
        elif adstock_type in [AdstockType.WEIBULL_CDF, AdstockType.WEIBULL_PDF]:
            adstock_result = self.transformation.adstock_weibull(
                channel,
                hyperparameters[f"{channel}_shape"],
                hyperparameters[f"{channel}_scale"],
                adstock_type=adstock_type.value,
            )
        else:
            raise ValueError(f"Unsupported adstock type: {adstock_type}")

        adstocked_data = adstock_result["adstocked"]

        # Apply saturation transformation
        saturated_data = self.transformation.saturation_hill(
            channel,
            hyperparameters[f"{channel}_alpha"],
            hyperparameters[f"{channel}_gamma"],
            marginal_input=adstocked_data,
        )

        return saturated_data

    def _prepare_model_data(self, hyperparameters: Dict[str, Any]) -> np.ndarray:
        """
        Prepare the model data by applying transformations to all channels.

        Args:
            hyperparameters (Dict[str, Any]): Hyperparameters for the transformations.

        Returns:
            np.ndarray: Prepared model data.
        """
        transformed_data = {}
        for channel in self.mmmdata.paid_media_vars:
            transformed_data[channel] = self._apply_transformations(channel, hyperparameters)

        # Combine transformed data with other variables
        model_data = np.column_stack(
            [transformed_data[channel] for channel in self.mmmdata.paid_media_vars]
            + [self.mmmdata.data[var] for var in self.mmmdata.context_vars + self.mmmdata.organic_vars]
        )

        return model_data
