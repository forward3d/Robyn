# ridge_model_builder.py
# pyre-strict
import warnings
import numpy as np
import pandas as pd
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass
from scipy.optimize import curve_fit
from sklearn.linear_model import Ridge
from sklearn.metrics import r2_score
import nevergrad as ng
from tqdm import tqdm
import time
from datetime import datetime
from robyn.modeling.convergence.convergence import Convergence
from sklearn.model_selection import train_test_split

# Add these imports
from sklearn.exceptions import ConvergenceWarning
from robyn.data.entities.calibration_input import CalibrationInput
from robyn.data.entities.holidays_data import HolidaysData
from robyn.data.entities.hyperparameters import Hyperparameters
from robyn.data.entities.mmmdata import MMMData
from robyn.modeling.entities.modeloutputs import ModelOutputs, Trial
from robyn.modeling.entities.modelrun_trials_config import TrialsConfig
from robyn.modeling.feature_engineering import FeaturizedMMMData
from robyn.modeling.entities.enums import NevergradAlgorithm
import io
import matplotlib.pyplot as plt
import seaborn as sns
import base64

# Add these warning filters at the top of your file
warnings.filterwarnings("ignore", category=UserWarning, module="pandas")
warnings.filterwarnings("ignore", category=ConvergenceWarning)
warnings.filterwarnings("ignore", category=np.RankWarning)


@dataclass
class ModelRefitOutput:
    rsq_train: float
    rsq_val: Optional[float]
    rsq_test: Optional[float]
    nrmse_train: float
    nrmse_val: Optional[float]
    nrmse_test: Optional[float]
    coefs: np.ndarray
    y_train_pred: np.ndarray
    y_val_pred: Optional[np.ndarray]
    y_test_pred: Optional[np.ndarray]
    y_pred: np.ndarray
    mod: Ridge
    df_int: int
    lambda_: float
    lambda_hp: float
    lambda_max: float
    lambda_min_ratio: float


class RidgeModelBuilder:
    def __init__(
        self,
        mmm_data: MMMData,
        holiday_data: HolidaysData,
        calibration_input: CalibrationInput,
        hyperparameters: Hyperparameters,
        featurized_mmm_data: FeaturizedMMMData,
    ):
        self.mmm_data = mmm_data
        self.holiday_data = holiday_data
        self.calibration_input = calibration_input
        self.hyperparameters = hyperparameters
        self.featurized_mmm_data = featurized_mmm_data

    def build_models(
        self,
        trials_config: TrialsConfig,
        dt_hyper_fixed: Optional[pd.DataFrame] = None,
        ts_validation: bool = False,
        add_penalty_factor: bool = False,
        seed: int = 123,
        rssd_zero_penalty: bool = True,
        objective_weights: Optional[List[float]] = None,
        nevergrad_algo: NevergradAlgorithm = NevergradAlgorithm.TWO_POINTS_DE,
        intercept: bool = True,
        intercept_sign: str = "non_negative",
        cores: int = 2,
    ) -> ModelOutputs:
        start_time = time.time()

        # Calculate intervalType and rollingWindowLength
        date_col = self.mmm_data.mmmdata_spec.date_var
        dates = pd.to_datetime(self.mmm_data.data[date_col])
        total_days = (dates.max() - dates.min()).days
        interval_days = (dates.iloc[1] - dates.iloc[0]).days

        if interval_days == 1:
            interval_type = "days"
        elif 6 <= interval_days <= 8:
            interval_type = "weeks"
        elif 28 <= interval_days <= 31:
            interval_type = "months"
        else:
            interval_type = f"{interval_days}-day periods"

        total_intervals = len(dates)
        rolling_window_length = (
            datetime.strptime(self.mmm_data.mmmdata_spec.window_end, "%Y-%m-%d")
            - datetime.strptime(self.mmm_data.mmmdata_spec.window_start, "%Y-%m-%d")
        ).days // interval_days + 1

        print(
            f"Input data has {total_intervals} {interval_type} in total: {dates.min().strftime('%Y-%m-%d')} to {dates.max().strftime('%Y-%m-%d')}"
        )
        print(
            f"Initial model is built on rolling window of {rolling_window_length} {interval_type}: {self.mmm_data.mmmdata_spec.window_start} to {self.mmm_data.mmmdata_spec.window_end}"
        )

        if ts_validation:
            print(
                f"Time-series validation with train_size range of {self.hyperparameters.train_size[0]*100:.0f}%-{self.hyperparameters.train_size[1]*100:.0f}% of the data..."
            )

        hyper_collect = self._hyper_collector(
            self.hyperparameters,
            ts_validation,
            add_penalty_factor,
            dt_hyper_fixed,
            cores,
        )

        print(
            f"Using {self.hyperparameters.adstock} adstocking with {len(self.hyperparameters.hyperparameters)} hyperparameters ({len(hyper_collect['hyper_bound_list_updated'])} to iterate + {len(hyper_collect['hyper_bound_list_fixed'])} fixed) on {cores} cores"
        )
        print(
            f">>> Starting {trials_config.trials} trials with {trials_config.iterations} iterations each using {nevergrad_algo.value} nevergrad algorithm..."
        )

        output_models = self._model_train(
            hyper_collect,
            trials_config,
            intercept_sign,
            intercept,
            nevergrad_algo,
            dt_hyper_fixed,
            ts_validation,
            add_penalty_factor,
            objective_weights,
            rssd_zero_penalty,
            seed,
            cores,
        )

        end_time = time.time()
        total_time = (end_time - start_time) / 60
        print(f"Total run time: {total_time:.2f} mins")
        convergence = Convergence()
        convergence_results = convergence.calculate_convergence(output_models)
        # Create ModelOutputs with all required arguments
        model_outputs = ModelOutputs(
            output_models,
            train_timestamp=datetime.now(),
            cores=cores,
            iterations=trials_config.iterations,
            intercept=intercept,
            intercept_sign=intercept_sign,
            nevergrad_algo=nevergrad_algo,
            ts_validation=ts_validation,
            add_penalty_factor=add_penalty_factor,
            hyper_updated=hyper_collect["hyper_list_all"],
            hyper_fixed=hyper_collect["all_fixed"],
            convergence=self.convergence_results,
            ts_validation_plot=(self._create_ts_validation_plot(output_models) if ts_validation else None),
            select_id=self._select_best_model(output_models),
            seed=seed,
            hyper_bound_ng=hyper_collect["hyper_bound_list_updated"],  # Add this line
            hyper_bound_fixed=hyper_collect["hyper_bound_list_fixed"],  # Add this line
        )

        # Print convergence information
        convergence_info = model_outputs.convergence
        print(f"Finished in {total_time:.2f} mins")
        for msg in convergence_info["conv_msg"]:
            print(f"- {msg}")

        return model_outputs

    # def _calculate_convergence(self, output_models: List[Trial]) -> Dict[str, Any]:
    #     # Extract nrmse and decomp.rssd from all trials
    #     nrmse_values = np.array([trial.nrmse for trial in output_models])
    #     decomp_rssd_values = np.array([trial.decomp_rssd for trial in output_models])

    #     # Calculate quantiles
    #     quantiles = [0.05, 0.10, 0.20, 0.30, 0.40, 0.50]
    #     cuts = [int(len(nrmse_values) * q) for q in quantiles]

    #     # Calculate errors for each quantile
    #     errors = {
    #         "nrmse": [np.mean(nrmse_values[:cut]) for cut in cuts],
    #         "decomp.rssd": [np.mean(decomp_rssd_values[:cut]) for cut in cuts],
    #         "cuts": cuts,
    #     }

    #     # Calculate standard deviations and medians at 20% quantile
    #     nrmse_sd_20 = np.std(nrmse_values[: cuts[2]])
    #     rssd_sd_20 = np.std(decomp_rssd_values[: cuts[2]])
    #     nrmse_med_20 = np.median(nrmse_values[: cuts[2]])
    #     rssd_med_20 = np.median(decomp_rssd_values[: cuts[2]])

    #     # Check convergence conditions
    #     nrmse_converged = (nrmse_sd_20 <= 0.055) and (abs(nrmse_med_20) <= 0.15)
    #     rssd_converged = (rssd_sd_20 <= 0.067) and (abs(rssd_med_20) <= 0.59)

    #     # Prepare convergence messages
    #     conv_msg = []
    #     if nrmse_converged and rssd_converged:
    #         conv_msg.append("NRMSE & DECOMP.RSSD converged")
    #     elif nrmse_converged:
    #         conv_msg.append(
    #             f"Only NRMSE converged: sd@qt.20 {nrmse_sd_20:.3f} <= 0.055 & |med@qt.20| {abs(nrmse_med_20):.2f} <= 0.15"
    #         )
    #         conv_msg.append(
    #             f"DECOMP.RSSD NOT converged: sd@qt.20 {rssd_sd_20:.3f} > 0.067 & |med@qt.20| {abs(rssd_med_20):.2f} <= 0.59"
    #         )
    #     elif rssd_converged:
    #         conv_msg.append(
    #             f"Only DECOMP.RSSD converged: sd@qt.20 {rssd_sd_20:.3f} <= 0.067 & |med@qt.20| {abs(rssd_med_20):.2f} <= 0.59"
    #         )
    #         conv_msg.append(
    #             f"NRMSE NOT converged: sd@qt.20 {nrmse_sd_20:.3f} > 0.055 & |med@qt.20| {abs(nrmse_med_20):.2f} <= 0.15"
    #         )
    #     else:
    #         conv_msg.append(f"Model has not converged:")
    #         conv_msg.append(
    #             f"NRMSE NOT converged: sd@qt.20 {nrmse_sd_20:.3f} > 0.055 & |med@qt.20| {abs(nrmse_med_20):.2f} <= 0.15"
    #         )
    #         conv_msg.append(
    #             f"DECOMP.RSSD NOT converged: sd@qt.20 {rssd_sd_20:.3f} > 0.067 & |med@qt.20| {abs(rssd_med_20):.2f} <= 0.59"
    #         )

    #     # Create plots
    #     moo_distrb_plot = self._create_moo_distrb_plot(nrmse_values, decomp_rssd_values)
    #     moo_cloud_plot = self._create_moo_cloud_plot(nrmse_values, decomp_rssd_values)

    #     return {
    #         "errors": errors,
    #         "conv_msg": conv_msg,
    #         "moo_distrb_plot": moo_distrb_plot,
    #         "moo_cloud_plot": moo_cloud_plot,
    #         "nrmse_converged": nrmse_converged,
    #         "rssd_converged": rssd_converged,
    #         "nrmse_sd_20": nrmse_sd_20,
    #         "rssd_sd_20": rssd_sd_20,
    #         "nrmse_med_20": nrmse_med_20,
    #         "rssd_med_20": rssd_med_20,
    #     }

    def _select_best_model(self, output_models: List[Trial]) -> str:
        # Extract relevant metrics
        nrmse_values = np.array([trial.nrmse for trial in output_models])
        decomp_rssd_values = np.array([trial.decomp_rssd for trial in output_models])

        # Normalize the metrics
        nrmse_norm = (nrmse_values - np.min(nrmse_values)) / (np.max(nrmse_values) - np.min(nrmse_values))
        decomp_rssd_norm = (decomp_rssd_values - np.min(decomp_rssd_values)) / (
            np.max(decomp_rssd_values) - np.min(decomp_rssd_values)
        )

        # Calculate the combined score (assuming equal weights)
        combined_score = nrmse_norm + decomp_rssd_norm

        # Find the index of the best model (lowest combined score)
        best_index = np.argmin(combined_score)

        # Return the solID of the best model
        return output_models[best_index].result_hyp_param["solID"].values[0]

    def _model_train(
        self,
        hyper_collect: Dict[str, Any],
        trials_config: TrialsConfig,
        intercept_sign: str,
        intercept: bool,
        nevergrad_algo: NevergradAlgorithm,
        dt_hyper_fixed: Optional[pd.DataFrame],
        ts_validation: bool,
        add_penalty_factor: bool,
        objective_weights: Optional[List[float]],
        rssd_zero_penalty: bool,
        seed: int,
        cores: int,
    ) -> List[Trial]:
        trials = []
        for trial in range(1, trials_config.trials + 1):
            trial_result = self._run_nevergrad_optimization(
                hyper_collect,
                trials_config.iterations,
                cores,
                nevergrad_algo,
                intercept,
                intercept_sign,
                ts_validation,
                add_penalty_factor,
                objective_weights,
                dt_hyper_fixed,
                rssd_zero_penalty,
                trial,
                seed + trial,
                trials_config.trials,
            )
            trials.append(trial_result)
        return trials

    def _run_nevergrad_optimization(
        self,
        hyper_collect: Dict[str, Any],
        iterations: int,
        cores: int,
        nevergrad_algo: NevergradAlgorithm,
        intercept: bool,
        intercept_sign: str,
        ts_validation: bool,
        add_penalty_factor: bool,
        objective_weights: Optional[List[float]],
        dt_hyper_fixed: Optional[pd.DataFrame],
        rssd_zero_penalty: bool,
        trial: int,
        seed: int,
        total_trials: int,
    ) -> Trial:
        warnings.filterwarnings("ignore", category=ConvergenceWarning)
        warnings.filterwarnings("ignore", category=RuntimeWarning)

        np.random.seed(seed)

        param_names = list(hyper_collect["hyper_bound_list_updated"].keys())
        param_bounds = [hyper_collect["hyper_bound_list_updated"][name] for name in param_names]

        instrum_dict = {
            name: ng.p.Scalar(lower=bound[0], upper=bound[1]) for name, bound in zip(param_names, param_bounds)
        }

        instrum = ng.p.Instrumentation(**instrum_dict)

        optimizer = ng.optimizers.registry[nevergrad_algo.value](instrum, budget=iterations, num_workers=cores)

        best_loss = float("inf")
        best_params = None
        best_nrmse = None
        best_decomp_rssd = None
        best_mape = None
        best_lift_calibration = None
        best_decomp_spend_dist = None
        best_x_decomp_agg = None
        best_rsq_train = None
        best_rsq_val = None
        best_rsq_test = None
        best_lambda = None
        best_lambda_hp = None
        best_lambda_max = None
        best_lambda_min_ratio = None
        best_pos = None
        best_elapsed = None
        best_elapsed_accum = None
        best_sol_id = None
        best_iter_ng = None
        best_iter_par = None

        start_time = time.time()
        with tqdm(
            total=iterations,
            desc=f"Running trial {trial} of total {total_trials} trials",
            bar_format="{l_bar}{bar}",
            ncols=75,
        ) as pbar:
            for iter_ng in range(iterations):
                candidate = optimizer.ask()
                params = candidate.kwargs
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    (
                        loss,
                        nrmse,
                        decomp_rssd,
                        mape,
                        lift_calibration,
                        decomp_spend_dist,
                        x_decomp_agg,
                        rsq_train,
                        rsq_val,
                        rsq_test,
                        lambda_,
                        lambda_hp,
                        lambda_max,
                        lambda_min_ratio,
                        pos,
                    ) = self._evaluate_model(
                        params, ts_validation, add_penalty_factor, rssd_zero_penalty, objective_weights
                    )
                optimizer.tell(candidate, loss)
                elapsed = time.time() - start_time
                if loss < best_loss:
                    best_loss = loss
                    best_params = params
                    best_params["solID"] = f"{trial}_{iter_ng + 1}_1"  # Add solID
                    best_nrmse = nrmse
                    best_decomp_rssd = decomp_rssd
                    best_mape = mape
                    best_lift_calibration = lift_calibration
                    best_decomp_spend_dist = decomp_spend_dist
                    best_x_decomp_agg = x_decomp_agg
                    best_rsq_train = rsq_train
                    best_rsq_val = rsq_val
                    best_rsq_test = rsq_test
                    best_lambda = lambda_
                    best_lambda_hp = lambda_hp
                    best_lambda_max = lambda_max
                    best_lambda_min_ratio = lambda_min_ratio
                    best_pos = pos
                    best_elapsed = elapsed
                    best_elapsed_accum = elapsed
                    best_sol_id = f"{trial}_{iter_ng + 1}_1"
                    best_iter_ng = iter_ng + 1
                    best_iter_par = 1
                pbar.update(1)

        end_time = time.time()
        print(f" Finished in {(end_time - start_time) / 60:.2f} mins")

        return Trial(
            result_hyp_param=pd.DataFrame([best_params]),
            lift_calibration=best_lift_calibration,
            decomp_spend_dist=best_decomp_spend_dist,
            nrmse=best_nrmse,
            decomp_rssd=best_decomp_rssd,
            mape=best_mape,
            x_decomp_agg=best_x_decomp_agg,
            rsq_train=best_rsq_train,
            rsq_val=best_rsq_val,
            rsq_test=best_rsq_test,
            lambda_=best_lambda,
            lambda_hp=best_lambda_hp,
            lambda_max=best_lambda_max,
            lambda_min_ratio=best_lambda_min_ratio,
            pos=best_pos,
            elapsed=best_elapsed,
            elapsed_accum=best_elapsed_accum,
            trial=trial,
            iter_ng=best_iter_ng,
            iter_par=best_iter_par,
            train_size=best_params.get("train_size", 1.0),
            sol_id=best_params["solID"],
        )

    def _prepare_data(self, params: Dict[str, float]) -> Tuple[pd.DataFrame, pd.Series]:
        # Get the dependent variable
        y = self.featurized_mmm_data.dt_mod[self.mmm_data.mmmdata_spec.dep_var]

        # Select all columns except the dependent variable
        X = self.featurized_mmm_data.dt_mod.drop(columns=[self.mmm_data.mmmdata_spec.dep_var])

        # Convert date columns to numeric (number of days since the earliest date)
        date_columns = X.select_dtypes(include=["datetime64", "object"]).columns
        for col in date_columns:
            X[col] = pd.to_datetime(X[col], errors="coerce", format="%Y-%m-%d")
            # Fill NaT (Not a Time) values with a default date (e.g., the minimum date in the column)
            min_date = X[col].min()
            X[col] = X[col].fillna(min_date)
            # Convert to days since minimum date, handling potential NaT values
            X[col] = (X[col] - min_date).dt.total_seconds().div(86400).fillna(0).astype(int)

        # One-hot encode categorical variables
        categorical_columns = X.select_dtypes(include=["object", "category"]).columns
        X = pd.get_dummies(X, columns=categorical_columns, drop_first=True)

        # Ensure all columns are numeric
        X = X.select_dtypes(include=[np.number])

        # Apply transformations based on hyperparameters
        for media in self.mmm_data.mmmdata_spec.paid_media_spends:
            if f"{media}_thetas" in params:
                X[media] = self._geometric_adstock(X[media], params[f"{media}_thetas"])
            if f"{media}_alphas" in params and f"{media}_gammas" in params:
                X[media] = self._hill_transformation(X[media], params[f"{media}_alphas"], params[f"{media}_gammas"])

        # Handle any remaining NaN or infinite values
        X = X.replace([np.inf, -np.inf], np.nan).fillna(0)
        y = y.replace([np.inf, -np.inf], np.nan).fillna(y.mean())
        X = X + 1e-8 * np.random.randn(*X.shape)

        return X, y

    def _geometric_adstock(self, x: pd.Series, theta: float) -> pd.Series:
        y = x.copy()
        for i in range(1, len(x)):
            y.iloc[i] += theta * y.iloc[i - 1]
        return y

    def _hill_transformation(self, x: pd.Series, alpha: float, gamma: float) -> pd.Series:
        x_scaled = (x - x.min()) / (x.max() - x.min())
        return x_scaled**alpha / (x_scaled**alpha + gamma**alpha)

    def _calculate_rssd(self, coefs: np.ndarray, rssd_zero_penalty: bool) -> float:
        rssd = np.sqrt(np.sum(coefs**2))
        if rssd_zero_penalty:
            zero_coef_ratio = np.sum(coefs == 0) / len(coefs)
            rssd *= 1 + zero_coef_ratio
        return rssd

    def _select_best_model(self, output_models: List[Trial]) -> str:
        # Extract relevant metrics
        nrmse_values = np.array([trial.nrmse for trial in output_models])
        decomp_rssd_values = np.array([trial.decomp_rssd for trial in output_models])

        # Normalize the metrics
        nrmse_norm = (nrmse_values - np.min(nrmse_values)) / (np.max(nrmse_values) - np.min(nrmse_values))
        decomp_rssd_norm = (decomp_rssd_values - np.min(decomp_rssd_values)) / (
            np.max(decomp_rssd_values) - np.min(decomp_rssd_values)
        )

        # Calculate the combined score (assuming equal weights)
        combined_score = nrmse_norm + decomp_rssd_norm

        # Find the index of the best model (lowest combined score)
        best_index = np.argmin(combined_score)

        # Return the solID of the best model
        return output_models[best_index].sol_id

    def _calculate_mape(self, model: Ridge) -> float:
        if self.calibration_input is None:
            return 0.0

        mape_values = []

        for _, calibration_data in self.calibration_input.items():
            # Extract relevant data
            df_raw = self.mmm_data.data
            wind_start = self.featurized_mmm_data.rollingWindowStartWhich
            wind_end = self.featurized_mmm_data.rollingWindowEndWhich
            dayInterval = self.mmm_data.mmmdata_spec.intervalType

            # Calculate lift using the calibration data
            lift_actual = calibration_data["liftStartDate"]
            lift_start = calibration_data["liftStartDate"]
            lift_end = calibration_data["liftEndDate"]
            lift_media = calibration_data["liftMedia"]

            # Filter data for the lift period
            df_lift = df_raw[
                (df_raw[self.mmm_data.mmmdata_spec.date_var] >= lift_start)
                & (df_raw[self.mmm_data.mmmdata_spec.date_var] <= lift_end)
            ]

            # Calculate predicted values
            X_lift = self._prepare_features(df_lift)
            y_pred = model.predict(X_lift)

            # Calculate predicted lift
            lift_pred = np.mean(y_pred) / np.mean(df_raw[self.mmm_data.mmmdata_spec.dep_var][wind_start:wind_end])

            # Calculate MAPE
            mape = np.abs((lift_actual - lift_pred) / lift_actual) * 100
            mape_values.append(mape)

        # Return mean MAPE across all calibration points
        return np.mean(mape_values)

    def _evaluate_model(
        self,
        params: Dict[str, float],
        ts_validation: bool,
        add_penalty_factor: bool,
        rssd_zero_penalty: bool,
        objective_weights: Optional[List[float]],
    ) -> Tuple[
        float,
        float,
        float,
        float,
        Optional[pd.DataFrame],
        Optional[pd.DataFrame],
        pd.DataFrame,
        float,
        float,
        float,
        float,
        float,
        float,
        float,
        int,
    ]:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
        X, y = self._prepare_data(params)

        if ts_validation:
            train_size = params.get("train_size", 0.8)
            X_train, X_test, y_train, y_test = train_test_split(X, y, train_size=train_size, shuffle=False)
            X_val, X_test, y_val, y_test = train_test_split(X_test, y_test, test_size=0.5, shuffle=False)
        else:
            X_train, y_train = X, y
            X_val, y_val = None, None
            X_test, y_test = None, None

        lambda_ = params.get("lambda", 1.0)
        model = Ridge(alpha=lambda_, fit_intercept=True)
        model.fit(X_train, y_train)

        y_train_pred = model.predict(X_train)
        nrmse_train = np.sqrt(np.mean((y_train - y_train_pred) ** 2)) / (y_train.max() - y_train.min())
        rsq_train = r2_score(y_train, y_train_pred)

        if ts_validation:
            y_val_pred = model.predict(X_val)
            y_test_pred = model.predict(X_test)
            nrmse_val = np.sqrt(np.mean((y_val - y_val_pred) ** 2)) / (y_val.max() - y_val.min())
            nrmse_test = np.sqrt(np.mean((y_test - y_test_pred) ** 2)) / (y_test.max() - y_test.min())
            rsq_val = r2_score(y_val, y_val_pred)
            rsq_test = r2_score(y_test, y_test_pred)
            nrmse = nrmse_val  # Use validation NRMSE for optimization
        else:
            y_val_pred = y_test_pred = None
            nrmse_val = nrmse_test = None
            rsq_val = rsq_test = 0.0
            nrmse = nrmse_train  # Use training NRMSE when not doing validation

        decomp_rssd = self._calculate_rssd(model.coef_, rssd_zero_penalty)

        mape = self._calculate_mape(model) if self.calibration_input else 0.0

        lift_calibration = None  # Implement this if needed
        decomp_spend_dist = None  # Implement this if needed

        lambda_hp = params.get("lambda", 0.0)
        lambda_max = self._lambda_seq(X_train, y_train)[0]
        lambda_min_ratio = 0.0001

        pos = np.all(model.coef_ >= 0)

        # Calculate x_decomp_agg
        x_decomp = X_train * model.coef_
        x_decomp_agg = pd.DataFrame(
            {
                "variable": X_train.columns,
                "coefficient": model.coef_,
                "sum": x_decomp.sum(),
                "mean": x_decomp.mean(),
                "median": x_decomp.median(),
            }
        )

        if objective_weights is None:
            objective_weights = [1 / 3, 1 / 3, 1 / 3] if self.calibration_input else [0.5, 0.5]

        loss = (
            objective_weights[0] * nrmse
            + objective_weights[1] * decomp_rssd
            + (objective_weights[2] * mape if self.calibration_input else 0)
        )

        return (
            loss,
            nrmse,
            decomp_rssd,
            mape,
            lift_calibration,
            decomp_spend_dist,
            x_decomp_agg,
            rsq_train,
            rsq_val,
            rsq_test,
            lambda_,
            lambda_hp,
            lambda_max,
            lambda_min_ratio,
            int(pos),
        )

    @staticmethod
    def _hyper_collector(
        hyperparameters: Hyperparameters,
        ts_validation: bool,
        add_penalty_factor: bool,
        dt_hyper_fixed: Optional[pd.DataFrame],
        cores: int,
    ) -> Dict[str, Any]:
        # Implement hyper_collector logic here
        # This should prepare the hyperparameters for optimization

        # Placeholder implementation
        hyper_collect = {
            "hyper_list_all": hyperparameters.hyperparameters,
            "hyper_bound_list_updated": {},
            "hyper_bound_list_fixed": {},
            "dt_hyper_fixed_mod": pd.DataFrame(),
            "all_fixed": False,
        }

        for name, value in hyperparameters.hyperparameters.items():
            if isinstance(value, list) and len(value) == 2:
                hyper_collect["hyper_bound_list_updated"][name] = value
            else:
                hyper_collect["hyper_bound_list_fixed"][name] = value

        if dt_hyper_fixed is not None:
            hyper_collect["dt_hyper_fixed_mod"] = dt_hyper_fixed
            hyper_collect["all_fixed"] = True

        return hyper_collect

    @staticmethod
    def _model_refit(
        x_train: np.ndarray,
        y_train: np.ndarray,
        x_val: Optional[np.ndarray] = None,
        y_val: Optional[np.ndarray] = None,
        x_test: Optional[np.ndarray] = None,
        y_test: Optional[np.ndarray] = None,
        lambda_: float = 1.0,
        lower_limits: Optional[List[float]] = None,
        upper_limits: Optional[List[float]] = None,
        intercept: bool = True,
        intercept_sign: str = "non_negative",
    ) -> ModelRefitOutput:
        model = Ridge(alpha=lambda_, fit_intercept=intercept)
        model.fit(x_train, y_train)

        y_train_pred = model.predict(x_train)
        y_val_pred = model.predict(x_val) if x_val is not None else None
        y_test_pred = model.predict(x_test) if x_test is not None else None

        rsq_train = r2_score(y_train, y_train_pred)
        rsq_val = r2_score(y_val, y_val_pred) if y_val is not None else None
        rsq_test = r2_score(y_test, y_test_pred) if y_test is not None else None

        nrmse_train = np.sqrt(np.mean((y_train - y_train_pred) ** 2)) / (np.max(y_train) - np.min(y_train))
        nrmse_val = (
            np.sqrt(np.mean((y_val - y_val_pred) ** 2)) / (np.max(y_val) - np.min(y_val))
            if y_val is not None
            else None
        )
        nrmse_test = (
            np.sqrt(np.mean((y_test - y_test_pred) ** 2)) / (np.max(y_test) - np.min(y_test))
            if y_test is not None
            else None
        )

        return ModelRefitOutput(
            rsq_train=rsq_train,
            rsq_val=rsq_val,
            rsq_test=rsq_test,
            nrmse_train=nrmse_train,
            nrmse_val=nrmse_val,
            nrmse_test=nrmse_test,
            coefs=model.coef_,
            y_train_pred=y_train_pred,
            y_val_pred=y_val_pred,
            y_test_pred=y_test_pred,
            y_pred=(
                np.concatenate([y_train_pred, y_val_pred, y_test_pred])
                if y_val is not None and y_test is not None
                else y_train_pred
            ),
            mod=model,
            df_int=1 if intercept else 0,
        )

    @staticmethod
    def _lambda_seq(
        x: np.ndarray,
        y: np.ndarray,
        seq_len: int = 100,
        lambda_min_ratio: float = 0.0001,
    ) -> np.ndarray:
        lambda_max = np.max(np.abs(np.sum(x * y, axis=0))) / (0.001 * x.shape[0])
        return np.logspace(np.log10(lambda_max * lambda_min_ratio), np.log10(lambda_max), num=seq_len)
