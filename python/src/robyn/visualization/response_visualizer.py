from pathlib import Path
from typing import Optional, Union
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import logging
from robyn.data.entities.mmmdata import MMMData
from robyn.modeling.entities.pareto_result import ParetoResult
from robyn.visualization.base_visualizer import BaseVisualizer

logger = logging.getLogger(__name__)


class ResponseVisualizer(BaseVisualizer):
    def __init__(self, pareto_result: ParetoResult, mmm_data: MMMData):
        super().__init__()
        logger.debug(
            "Initializing ResponseVisualizer with pareto_result=%s, mmm_data=%s",
            pareto_result,
            mmm_data,
        )
        self.pareto_result = pareto_result
        self.mmm_data = mmm_data

    def plot_response(self) -> plt.Figure:
        """
        Plot response curves.

        Returns:
            plt.Figure: The generated figure.
        """
        logger.info("Starting response curve plotting")
        pass

    def plot_marginal_response(self) -> plt.Figure:
        """
        Plot marginal response curves.

        Returns:
            plt.Figure: The generated figure.
        """
        logger.info("Starting marginal response curve plotting")
        pass

    def generate_response_curves(
       self, solution_id: str, ax: Optional[plt.Axes] = None, trim_rate: float = 1.3
   ) -> Optional[plt.Figure]:
       """Generate response curves showing relationship between spend and response."""
       logger.debug("Generating response curves with trim_rate=%.2f", trim_rate)

       if solution_id not in self.pareto_result.plot_data_collect:
           raise ValueError(f"Invalid solution ID: {solution_id}")

       try:
           # Get plot data for specific solution
           logger.debug("Extracting plot data from pareto results")
           plot_data = self.pareto_result.plot_data_collect[solution_id]
           curve_data = plot_data["plot4data"]["dt_scurvePlot"].copy()
           mean_data = plot_data["plot4data"]["dt_scurvePlotMean"].copy()

           logger.debug("Initial curve data shape: %s", curve_data.shape)
           logger.debug("Initial mean data shape: %s", mean_data.shape)

           # Scale down the values to thousands
           curve_data["spend"] = curve_data["spend"] / 1000
           curve_data["response"] = curve_data["response"] / 1000
           mean_data["mean_spend_adstocked"] = mean_data["mean_spend_adstocked"] / 1000
           mean_data["mean_response"] = mean_data["mean_response"] / 1000
           if "mean_carryover" in mean_data.columns:
               mean_data["mean_carryover"] = mean_data["mean_carryover"] / 1000

           # Add mean carryover information
           curve_data = curve_data.merge(
               mean_data[["channel", "mean_carryover"]], on="channel", how="left"
           )

           if ax is None:
               logger.debug("Creating new figure with axes")
               fig, ax = plt.subplots(figsize=(16, 10))
           else:
               logger.debug("Using provided axes")
               fig = None

           channels = curve_data["channel"].unique()
           logger.debug("Processing %d unique channels: %s", len(channels), channels)
           colors = plt.cm.Set2(np.linspace(0, 1, len(channels)))

           for idx, channel in enumerate(channels):
               logger.debug("Plotting response curve for channel: %s", channel)
               channel_data = curve_data[curve_data["channel"] == channel].sort_values(
                   "spend"
               )

               ax.plot(
                   channel_data["spend"],
                   channel_data["response"],
                   color=colors[idx],
                   label=channel,
                   zorder=2,
               )

               if "mean_carryover" in channel_data.columns:
                   logger.debug("Adding carryover shading for channel: %s", channel)
                   carryover_data = channel_data[
                       channel_data["spend"] <= channel_data["mean_carryover"].iloc[0]
                   ]
                   ax.fill_between(
                       carryover_data["spend"],
                       carryover_data["response"],
                       color="grey",
                       alpha=0.2,
                       zorder=1,
                   )

           logger.debug("Adding mean points and labels")
           for idx, row in mean_data.iterrows():
               ax.scatter(
                   row["mean_spend_adstocked"],
                   row["mean_response"],
                   color=colors[idx],
                   s=100,
                   zorder=3,
               )

               # Format scaled values in thousands
               formatted_spend = f"{row['mean_spend_adstocked']:.1f}K"

               ax.text(
                   row["mean_spend_adstocked"],
                   row["mean_response"],
                   formatted_spend,
                   ha="left",
                   va="bottom",
                   fontsize=9,
                   color=colors[idx],
               )

           logger.debug("Formatting axis labels")

           # Formatter for values in thousands
           def format_axis_labels(x, p):
               return f"{x:.0f}K"

           ax.xaxis.set_major_formatter(plt.FuncFormatter(format_axis_labels))
           ax.yaxis.set_major_formatter(plt.FuncFormatter(format_axis_labels))

           ax.grid(True, alpha=0.2)
           ax.set_axisbelow(True)
           ax.spines["top"].set_visible(False)
           ax.spines["right"].set_visible(False)

           ax.set_title(
               f"Response Curves and Mean Spends by Channel (Solution {solution_id})"
           )
           ax.set_xlabel("Spend in Thousands (carryover + immediate)")
           ax.set_ylabel("Response in Thousands")

           ax.legend(
               bbox_to_anchor=(1.02, 0.5),
               loc="center left",
               frameon=True,
               framealpha=0.8,
               facecolor="white",
               edgecolor="none",
           )

           if fig:
               logger.debug("Adjusting layout")
               plt.tight_layout()
               logger.debug("Successfully generated response curves figure")
               return fig

           logger.debug("Successfully added response curves to existing axes")
           return None

       except Exception as e:
           logger.error("Error generating response curves: %s", str(e), exc_info=True)
           raise

    def plot_all(
        self, display_plots: bool = True, export_location: Union[str, Path] = None
    ) -> None:
        pass
