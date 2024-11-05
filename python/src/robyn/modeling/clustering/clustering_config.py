# pyre-strict

from dataclasses import dataclass
from enum import Enum
from sys import maxsize
from typing import List, Literal, Optional

from robyn.data.entities.enums import DependentVarType


class ClusterBy(Enum):
    PERFORMANCE = "performance"
    HYPERPARAMETERS = "hyperparameters"


@dataclass
class ClusteringConfig:
    """
    Configuration for the clustering process.

    Attributes:
        dep_var_type (DependentVarType): Type of dependent variable (revenue or conversion).
        cluster_by (ClusterBy): Attribute to cluster by (performance or hyperparameters).
        max_clusters (int): Maximum number of clusters to consider.
        limit (int): Top N results per cluster.
        weights (List[float]): Weights for NRMSE, DECOMP.RSSD, and MAPE errors.
        dim_reduction (Literal["PCA", "tSNE"]): Dimensionality reduction technique.
        export (bool): Whether to export results.
        seed (int): Random seed for reproducibility.
    """

    weights: List[float]
    dep_var_type: DependentVarType
    cluster_by: ClusterBy = ClusterBy.HYPERPARAMETERS
    max_clusters: int = 30
    min_clusters: int = 3
    k_clusters: int = maxsize
    limit: int = 1
    dim_reduction: Literal["PCA", "tSNE"] = "PCA"
    export: bool = False
    seed: int = 123
    all_media: Optional[List[str]] = None
