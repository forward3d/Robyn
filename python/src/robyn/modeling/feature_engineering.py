# pyre-strict

from typing import Optional, Dict, Any
import logging
import pandas as pd
import warnings
from scipy.optimize import curve_fit
from sklearn.linear_model import LinearRegression
import numpy as np

np.float_ = np.float64
from prophet import Prophet
from robyn.data.entities.holidays_data import HolidaysData

from robyn.data.entities.enums import (
    AdstockType,
)
from robyn.modeling.entities.featurized_mmm_data import FeaturizedMMMData
from robyn.data.entities.hyperparameters import Hyperparameters, ChannelHyperparameters
from robyn.data.entities.mmmdata import MMMData


class FeatureEngineering:
    """
    A class used to perform feature engineering for Marketing Mix Modeling (MMM) data.
    """

    def __init__(
        self, mmm_data: MMMData, hyperparameters: Hyperparameters, holidays_data: Optional[HolidaysData] = None
    ):
        self.mmm_data = mmm_data
        self.hyperparameters = hyperparameters
        self.holidays_data = holidays_data
        self.logger = logging.getLogger(__name__)
        self.logger.debug("Initializing FeatureEngineering with MMM data and hyperparameters")

    def perform_feature_engineering(self, quiet: bool = False) -> FeaturizedMMMData:
        self.logger.info("Starting feature engineering process")
        self.logger.debug(f"Input data shape: {self.mmm_data.data.shape}")
        
        dt_transform = self._prepare_data()
        self.logger.debug(f"Prepared data shape: {dt_transform.shape}")

        if any(var in self.holidays_data.prophet_vars for var in ["trend", "season", "holiday", "monthly", "weekday"]):
            self.logger.info("Starting Prophet decomposition")
            dt_transform = self._prophet_decomposition(dt_transform)
            if not quiet:
                self.logger.info("Prophet decomposition complete")

        # Include all independent variables
        all_ind_vars = (
            self.holidays_data.prophet_vars
            + self.mmm_data.mmmdata_spec.context_vars
            + self.mmm_data.mmmdata_spec.paid_media_spends
            + self.mmm_data.mmmdata_spec.organic_vars
        )
        self.logger.debug(f"Processing {len(all_ind_vars)} independent variables")

        dt_mod = dt_transform
        dt_modRollWind = self._create_rolling_window_data(dt_transform)
        self.logger.debug(f"Rolling window data shape: {dt_modRollWind.shape}")

        media_cost_factor = self._calculate_media_cost_factor(dt_modRollWind)
        self.logger.debug("Calculated media cost factors")

        modNLS = self._run_models(dt_modRollWind, media_cost_factor)
        self.logger.debug(f"Completed model runs for {len(modNLS['results'])} channels")

        columns_to_keep = ["ds", "dep_var"] + all_ind_vars
        # Only keep columns that exist in both dataframes
        columns_to_keep = [col for col in columns_to_keep if col in dt_mod.columns and col in dt_modRollWind.columns]
        self.logger.debug(f"Keeping {len(columns_to_keep)} columns in final dataset")

        dt_mod = dt_transform[columns_to_keep]
        dt_modRollWind = dt_modRollWind[columns_to_keep]

        if not quiet:
            self.logger.info("Feature engineering complete")

        # Fill or interpolate missing values in dt_mod
        missing_before = dt_mod.isnull().sum().sum()
        dt_mod = dt_mod.fillna(method="ffill").fillna(method="bfill")
        missing_after = dt_mod.isnull().sum().sum()
        self.logger.info(f"Filled {missing_before - missing_after} missing values")

        return FeaturizedMMMData(dt_mod=dt_mod, dt_modRollWind=dt_modRollWind, modNLS=modNLS)

    def _prepare_data(self) -> pd.DataFrame:
        self.logger.debug("Starting data preparation")
        dt_transform = self.mmm_data.data.copy()
        dt_transform["ds"] = pd.to_datetime(dt_transform[self.mmm_data.mmmdata_spec.date_var]).dt.strftime("%Y-%m-%d")
        dt_transform["dep_var"] = dt_transform[self.mmm_data.mmmdata_spec.dep_var]
        dt_transform["competitor_sales_B"] = dt_transform["competitor_sales_B"].astype("int64")
        self.logger.debug("Data preparation complete")
        return dt_transform

    def _create_rolling_window_data(self, dt_transform: pd.DataFrame) -> pd.DataFrame:
        self.logger.debug("Creating rolling window data")
        window_start = self.mmm_data.mmmdata_spec.window_start
        window_end = self.mmm_data.mmmdata_spec.window_end

        try:
            if window_start is None and window_end is None:
                self.logger.debug("No window constraints specified")
                return dt_transform
            elif window_start is None:
                if window_end < dt_transform["ds"].min():
                    self.logger.error("Window end date is before the start of data")
                    raise ValueError("window_end is before the start of the data")
                self.logger.debug(f"Filtering data up to {window_end}")
                return dt_transform[dt_transform["ds"] <= window_end]
            elif window_end is None:
                if window_start > dt_transform["ds"].max():
                    self.logger.error("Window start date is after the end of data")
                    raise ValueError("window_start is after the end of the data")
                self.logger.debug(f"Filtering data from {window_start}")
                return dt_transform[dt_transform["ds"] >= window_start]
            else:
                if window_start > window_end:
                    self.logger.error(f"Invalid window range: {window_start} to {window_end}")
                    raise ValueError("window_start is after window_end")
                self.logger.debug(f"Filtering data between {window_start} and {window_end}")
                return dt_transform[(dt_transform["ds"] >= window_start) & (dt_transform["ds"] <= window_end)]
        except Exception as e:
            self.logger.error(f"Error creating rolling window data: {str(e)}")
            raise

    def _calculate_media_cost_factor(self, dt_input_roll_wind: pd.DataFrame) -> pd.Series:
        self.logger.debug("Calculating media cost factors")
        total_spend = dt_input_roll_wind[self.mmm_data.mmmdata_spec.paid_media_spends].sum().sum()
        return dt_input_roll_wind[self.mmm_data.mmmdata_spec.paid_media_spends].sum() / total_spend

    def _run_models(self, dt_modRollWind: pd.DataFrame, media_cost_factor: float) -> Dict[str, Dict[str, Any]]:
        self.logger.info("Starting model runs for paid media variables")
        modNLS = {"results": {}, "yhat": pd.DataFrame(), "plots": {}}

        for paid_media_var in self.mmm_data.mmmdata_spec.paid_media_spends:
            self.logger.debug(f"Processing model for {paid_media_var}")
            result = self._fit_spend_exposure(dt_modRollWind, paid_media_var, media_cost_factor)
            if result is not None:
                modNLS["results"][paid_media_var] = result["res"]
                modNLS["yhat"] = pd.concat([modNLS["yhat"], result["plot"]], ignore_index=True)
                modNLS["plots"][paid_media_var] = result["plot"]
                self.logger.debug(f"Completed model fit for {paid_media_var} with R² = {result['res']['rsq']:.4f}")
            else:
                self.logger.warning(f"Model fitting failed for {paid_media_var}")

        self.logger.info(f"Completed model runs for {len(modNLS['results'])} channels")
        return modNLS

    def _fit_spend_exposure(
        self, dt_modRollWind: pd.DataFrame, paid_media_var: str, media_cost_factor: float
    ) -> Dict[str, Any]:
        self.logger.info(f"Fitting spend-exposure model for {paid_media_var}")

        def michaelis_menten(x, Vmax, Km):
            return Vmax * x / (Km + x)

        spend_var = paid_media_var
        exposure_var = self.mmm_data.mmmdata_spec.paid_media_vars[
            self.mmm_data.mmmdata_spec.paid_media_spends.index(paid_media_var)
        ]

        spend_data = dt_modRollWind[spend_var]
        exposure_data = dt_modRollWind[exposure_var]

        if exposure_data.empty:
            self.logger.warning(f"Exposure data for {paid_media_var} is empty. Skipping model fitting.")
            return None

        try:
            self.logger.debug(f"Fitting Michaelis-Menten model for {paid_media_var}")
            popt_nls, _ = curve_fit(
                michaelis_menten,
                spend_data,
                exposure_data,
                p0=[max(exposure_data), np.median(spend_data)],
                bounds=([0, 0], [np.inf, np.inf]),
                maxfev=10000,
            )

            yhat_nls = michaelis_menten(spend_data, *popt_nls)
            rsq_nls = 1 - np.sum((exposure_data - yhat_nls) ** 2) / np.sum(
                (exposure_data - np.mean(exposure_data)) ** 2
            )
            self.logger.debug(f"Michaelis-Menten model R² = {rsq_nls:.4f}")

            self.logger.debug(f"Fitting linear model for {paid_media_var}")
            lm = LinearRegression(fit_intercept=False)
            lm.fit(spend_data.values.reshape(-1, 1), exposure_data)
            yhat_lm = lm.predict(spend_data.values.reshape(-1, 1))
            rsq_lm = lm.score(spend_data.values.reshape(-1, 1), exposure_data)
            self.logger.debug(f"Linear model R² = {rsq_lm:.4f}")

            if rsq_nls > rsq_lm:
                self.logger.info(f"Selected Michaelis-Menten model for {paid_media_var} (R² = {rsq_nls:.4f})")
                model_type = "nls"
                yhat = yhat_nls
                rsq = rsq_nls
                coef = {"Vmax": popt_nls[0], "Km": popt_nls[1]}
            else:
                self.logger.info(f"Selected linear model for {paid_media_var} (R² = {rsq_lm:.4f})")
                model_type = "lm"
                yhat = yhat_lm
                rsq = rsq_lm
                coef = {"coef": lm.coef_[0]}

            res = {"channel": paid_media_var, "model_type": model_type, "rsq": rsq, "coef": coef}
            plot_data = pd.DataFrame({"spend": spend_data, "exposure": exposure_data, "yhat": yhat})

            return {"res": res, "plot": plot_data, "yhat": yhat}

        except Exception as e:
            self.logger.warning(f"Error fitting models for {paid_media_var}: {str(e)}. Falling back to linear model.")
            lm = LinearRegression(fit_intercept=False)
            lm.fit(spend_data.values.reshape(-1, 1), exposure_data)
            yhat_lm = lm.predict(spend_data.values.reshape(-1, 1))
            rsq_lm = lm.score(spend_data.values.reshape(-1, 1), exposure_data)
            
            res = {"channel": paid_media_var, "model_type": "lm", "rsq": rsq_lm, "coef": {"coef": lm.coef_[0]}}
            plot_data = pd.DataFrame({"spend": spend_data, "exposure": exposure_data, "yhat": yhat_lm})
            
            return {"res": res, "plot": plot_data, "yhat": yhat_lm}

    def _prophet_decomposition(self, dt_mod: pd.DataFrame) -> pd.DataFrame:
        self.logger.info("Starting Prophet decomposition")
        prophet_vars = self.holidays_data.prophet_vars
        self.logger.debug(f"Prophet variables to process: {prophet_vars}")

        recurrence = dt_mod[["ds", "dep_var"]].rename(columns={"dep_var": "y"}).copy()
        recurrence["ds"] = pd.to_datetime(recurrence["ds"])

        holidays = self._set_holidays(
            dt_mod, self.holidays_data.dt_holidays.copy(), self.mmm_data.mmmdata_spec.interval_type
        ).copy()

        use_trend = "trend" in prophet_vars
        use_holiday = "holiday" in prophet_vars
        use_season = "season" in prophet_vars or "yearly.seasonality" in prophet_vars
        use_monthly = "monthly" in prophet_vars
        use_weekday = "weekday" in prophet_vars or "weekly.seasonality" in prophet_vars

        self.logger.debug(
            f"Prophet components enabled - Trend: {use_trend}, Holiday: {use_holiday}, "
            f"Season: {use_season}, Monthly: {use_monthly}, Weekday: {use_weekday}"
        )

        dt_regressors = pd.concat(
            [
                recurrence,
                dt_mod[
                    self.mmm_data.mmmdata_spec.paid_media_spends
                    + self.mmm_data.mmmdata_spec.context_vars
                    + self.mmm_data.mmmdata_spec.organic_vars
                ].copy(),
            ],
            axis=1,
        )
        dt_regressors["ds"] = pd.to_datetime(dt_regressors["ds"])

        prophet_country = self.holidays_data.prophet_country
        if isinstance(prophet_country, str):
            prophet_country = [prophet_country]

        if use_holiday and holidays is not None:
            holidays_filtered = holidays[holidays["country"].isin(prophet_country)].copy()
            self.logger.debug(f"Filtered holidays for countries: {prophet_country}")
        else:
            holidays_filtered = None

        try:
            prophet_params = {
                "holidays": holidays_filtered,
                "yearly_seasonality": use_season,
                "weekly_seasonality": use_weekday,
                "daily_seasonality": False,
            }
            if hasattr(self, "custom_params"):
                if "yearly.seasonality" in self.custom_params:
                    prophet_params["yearly_seasonality"] = self.custom_params["yearly.seasonality"]
                if "weekly.seasonality" in self.custom_params and self.mmm_data.mmmdata_spec.day_interval <= 7:
                    prophet_params["weekly_seasonality"] = self.custom_params["weekly.seasonality"]

            model = Prophet(**prophet_params)
            if use_monthly:
                self.logger.debug("Adding monthly seasonality to Prophet model")
                model.add_seasonality(name="monthly", period=30.5, fourier_order=5)

            if self.mmm_data.mmmdata_spec.factor_vars:
                self.logger.debug(f"Processing factor variables: {self.mmm_data.mmmdata_spec.factor_vars}")
                dt_ohe = pd.get_dummies(dt_regressors[self.mmm_data.mmmdata_spec.factor_vars], drop_first=False)
                ohe_names = [col for col in dt_ohe.columns if col not in self.mmm_data.mmmdata_spec.factor_vars]
                
                self.logger.debug(f"Adding {len(ohe_names)} one-hot encoded regressors to Prophet model")
                for addreg in ohe_names:
                    model.add_regressor(addreg)
                
                dt_ohe = pd.concat([dt_regressors.drop(columns=self.mmm_data.mmmdata_spec.factor_vars), dt_ohe], axis=1)
                self.logger.info("Fitting Prophet model with factor variables")
                mod_ohe = model.fit(dt_ohe)
                dt_forecastRegressor = mod_ohe.predict(dt_ohe)
                
                forecastRecurrence = dt_forecastRegressor.drop(
                    columns=[col for col in dt_forecastRegressor.columns if "_lower" in col or "_upper" in col]
                )
                
                for aggreg in self.mmm_data.mmmdata_spec.factor_vars:
                    self.logger.debug(f"Processing aggregation for {aggreg}")
                    oheRegNames = [col for col in forecastRecurrence.columns if col.startswith(f"{aggreg}_")]
                    get_reg = forecastRecurrence[oheRegNames].sum(axis=1)
                    dt_mod[aggreg] = (get_reg - get_reg.min()) / (get_reg.max() - get_reg.min())
            else:
                if self.mmm_data.mmmdata_spec.day_interval == 1:
                    self.logger.warning(
                        "Known issue with Prophet for daily intervals. See: https://github.com/facebookexperimental/Robyn/issues/472"
                    )
                self.logger.info("Fitting Prophet model without factor variables")
                mod = model.fit(dt_regressors)
                forecastRecurrence = mod.predict(dt_regressors)

            these = range(len(recurrence))
            self.logger.debug("Applying Prophet decomposition components")
            
            if use_trend:
                dt_mod["trend"] = forecastRecurrence["trend"].iloc[these].values
                self.logger.debug("Added trend component")
            if use_season:
                dt_mod["season"] = forecastRecurrence["yearly"].iloc[these].values
                self.logger.debug("Added seasonal component")
            if use_monthly:
                dt_mod["monthly"] = forecastRecurrence["monthly"].iloc[these].values
                self.logger.debug("Added monthly component")
            if use_weekday:
                dt_mod["weekday"] = forecastRecurrence["weekly"].iloc[these].values
                self.logger.debug("Added weekday component")
            if use_holiday:
                dt_mod["holiday"] = forecastRecurrence["holidays"].iloc[these].values
                self.logger.debug("Added holiday component")

            self.logger.info("Prophet decomposition completed successfully")
            return dt_mod

        except Exception as e:
            self.logger.error(f"Error during Prophet decomposition: {str(e)}")
            raise

    def _set_holidays(self, dt_transform: pd.DataFrame, dt_holidays: pd.DataFrame, interval_type: str) -> pd.DataFrame:
        self.logger.debug(f"Setting holidays for interval type: {interval_type}")
        
        try:
            dt_transform["ds"] = pd.to_datetime(dt_transform["ds"])
            holidays = dt_holidays.copy()
            holidays["ds"] = pd.to_datetime(holidays["ds"])

            if interval_type == "day":
                self.logger.debug("Using daily holiday data")
                return holidays
            elif interval_type == "week":
                self.logger.debug("Processing weekly holiday aggregation")
                week_start = dt_transform["ds"].dt.weekday[0]
                holidays["ds"] = (
                    holidays["ds"] - pd.to_timedelta(holidays["ds"].dt.weekday, unit="D") + pd.Timedelta(days=week_start)
                )
                holidays = (
                    holidays.groupby(["ds", "country", "year"])
                    .agg(holiday=("holiday", lambda x: ", ".join(x)), n=("holiday", "count"))
                    .reset_index()
                )
                self.logger.debug(f"Aggregated {len(holidays)} weekly holiday entries")
                return holidays
            elif interval_type == "month":
                self.logger.debug("Processing monthly holiday aggregation")
                if not all(dt_transform["ds"].dt.day == 1):
                    self.logger.error("Monthly data does not start on first day of month")
                    raise ValueError("Monthly data should have first day of month as datestamp, e.g.'2020-01-01'")
                holidays["ds"] = holidays["ds"].dt.to_period("M").dt.to_timestamp()
                holidays = holidays.groupby(["ds", "country", "year"])["holiday"].agg(lambda x: ", ".join(x)).reset_index()
                self.logger.debug(f"Aggregated {len(holidays)} monthly holiday entries")
                return holidays
            else:
                self.logger.error(f"Invalid interval_type: {interval_type}")
                raise ValueError("Invalid interval_type. Must be 'day', 'week', or 'month'.")

        except Exception as e:
            self.logger.error(f"Error setting holidays: {str(e)}")
            raise

    def _apply_transformations(self, x: pd.Series, params: ChannelHyperparameters) -> pd.Series:
        self.logger.debug("Applying transformations to series")
        x_adstock = self._apply_adstock(x, params)
        x_saturated = self._apply_saturation(x_adstock, params)
        return x_saturated

    def _apply_adstock(self, x: pd.Series, params: ChannelHyperparameters) -> pd.Series:
        self.logger.debug(f"Applying {self.hyperparameters.adstock} adstock transformation")
        try:
            if self.hyperparameters.adstock == AdstockType.GEOMETRIC:
                return self._geometric_adstock(x, params.thetas[0])
            elif self.hyperparameters.adstock in [AdstockType.WEIBULL_CDF, AdstockType.WEIBULL_PDF]:
                return self._weibull_adstock(x, params.shapes[0], params.scales[0])
            else:
                self.logger.error(f"Unsupported adstock type: {self.hyperparameters.adstock}")
                raise ValueError(f"Unsupported adstock type: {self.hyperparameters.adstock}")
        except Exception as e:
            self.logger.error(f"Error applying adstock transformation: {str(e)}")
            raise

    @staticmethod
    def _geometric_adstock(x: pd.Series, theta: float) -> pd.Series:
        return x.ewm(alpha=1 - theta, adjust=False).mean()

    @staticmethod
    def _weibull_adstock(x: pd.Series, shape: float, scale: float) -> pd.Series:
        def weibull_pdf(t):
            return (shape / scale) * ((t / scale) ** (shape - 1)) * np.exp(-((t / scale) ** shape))

        weights = [weibull_pdf(t) for t in range(1, len(x) + 1)]
        weights = weights / np.sum(weights)
        return np.convolve(x, weights[::-1], mode="full")[: len(x)]

    def _apply_saturation(self, x: pd.Series, params: ChannelHyperparameters) -> pd.Series:
        self.logger.debug("Applying saturation transformation")
        try:
            alpha, gamma = params.alphas[0], params.gammas[0]
            return x**alpha / (x**alpha + gamma**alpha)
        except Exception as e:
            self.logger.error(f"Error applying saturation transformation: {str(e)}")
            raise