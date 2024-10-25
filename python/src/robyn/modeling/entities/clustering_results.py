# pyre-strict
from dataclasses import dataclass
from typing import Dict, List, Optional

import matplotlib.pyplot as plt

import pandas as pd


@dataclass
class ClusterPlotResults:
    """
    Represents the plots generated during the clustering process.

    Attributes:
        wss_plot (Optional[plt.Figure]): Plot related to Within Groups Sum of Squares.
        correlations_plot (Optional[plt.Figure]): Plot showing grouped correlations per cluster.
        clusters_means_plot (Optional[plt.Figure]): Plot showing mean ROI per cluster.
        top_solutions_errors_plot (Optional[plt.Figure]): Plot for top solutions based on errors.
        top_solutions_rois_plot (Optional[plt.Figure]): Plot for top solutions based on ROI.
        plot_clusters_ci (Optional[plt.Figure]): Plot for confidence intervals of clusters.
    """

    wss_plot: Optional[plt.Figure] = None
    correlations_plot: Optional[plt.Figure] = None
    clusters_means_plot: Optional[plt.Figure] = None
    top_solutions_errors_plot: Optional[plt.Figure] = None
    top_solutions_rois_plot: Optional[plt.Figure] = None
    plot_clusters_ci: Optional[plt.Figure] = None



@dataclass
class ClusterConfidenceIntervals:
    """
    Represents the confidence intervals for the clusters.

    Attributes:
        cluster_ci (pd.DataFrame): The DataFrame containing confidence intervals for the clusters.
        boot_n (int): The number of bootstrap samples used.
        sim_n (int): The number of simulations performed.
    """

    cluster_ci: pd.DataFrame
    boot_n: int
    sim_n: int


@dataclass
class ClusteredResult:
    """
    Represents the overall results of the clustering process.

    Attributes:
        cluster_data (pd.DataFrame): The DataFrame containing the clustered models.
        top_solutions (pd.DataFrame): The top solutions based on clustering.
        cluster_ci (ClusterConfidenceIntervals): The confidence intervals for the clusters.
        n_clusters (int): The number of clusters created.
        errors_weights (List[float]): The weights used for error calculations.
        clusters_means (pd.DataFrame): Mean ROI per cluster.
        clusters_pca (pd.DataFrame): Data related to PCA clusters.
        clusters_tsne (pd.DataFrame): Data related to t-SNE clusters.
        correlations (pd.DataFrame): Grouped correlations per cluster.
        plots (PlotResults): An instance of PlotResults containing all generated plots.
    """

    cluster_data: pd.DataFrame
    top_solutions: pd.DataFrame
    cluster_ci: ClusterConfidenceIntervals
    n_clusters: int
    errors_weights: List[float]
    clusters_means: pd.DataFrame
    clusters_pca: pd.DataFrame
    clusters_tsne: pd.DataFrame
    correlations: pd.DataFrame
    plots: ClusterPlotResults
