import base64
import io
import logging
import os
from typing import Union

import anndata as ad
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scanpy as sc
from jinja2 import Template
from scipy.sparse import csr_matrix
from spacemake.config import ConfigFile
from spacemake.project_df import ProjectDF
from spacemake.util import message_aggregation

absolute_path = os.path.dirname(__file__)

cpalette = {
    "grey": "#999999",
    "light_orange": "#E69F00",
    "light_blue": "#56B4E9",
    "green": "#009E73",
    "yellow": "#F0E442",
    "blue": "#0072B2",
    "orange": "#D55E00",
    "pink": "#CC79A7",
}

clrs = {
    "umis": cpalette["light_orange"],
    "genes": cpalette["light_blue"],
    "reads": cpalette["green"],
    "pcr": cpalette["pink"],
    "pct_counts_mt": "black",
}

nucl_clrs = {
    "A": "#F5C900",
    "C": "#F55D59",
    "T": "#3AA861",
    "G": "#7772F5",
    "N": "#999999",
}

SAMPLEINFO_VARS = [
    "species",
    "sequencing_date",
    "investigator",
    "experiment",
]
SPATIAL_METRICS = [
    "n_genes_by_counts",
    "total_counts",
    "pct_counts_mt",
    "n_reads",
    "reads_per_counts",
    "n_joined",
    "exact_entropy",
    "exact_compression",
]
SPATIAL_METRICS_TITLES = {
    "n_genes_by_counts": "# of genes per spatial unit",
    "total_counts": "# of UMIs per spatial unit",
    "pct_counts_mt": "# % mt counts per spatial unit",
    "n_reads": "# of reads per spatial unit",
    "reads_per_counts": "reads/UMI per spatial unit",
    "n_joined": "# beads joined per spatial unit",
    "exact_entropy": "Shannon entropy per spatial unit",
    "exact_compression": "barcode length after compression per spatial unit",
}


STRTOBOOL = {
    "False": False,
    "True": True
}


logger_name = "spacemake.report.qc_sequencing"
logger = logging.getLogger(logger_name)


def setup_parser(parser):
    """
    Set up command-line arguments for the script.

    :param parser: Argument parser object.
    :type parser: argparse.ArgumentParser
    :returns: Updated argument parser object.
    :rtype: argparse.ArgumentParser
    """
    # These allow to get the run_mode_variables.* from the config file
    # and pbf_metrics.px_by_um via project_df.puck_barcode_file_metrics
    parser.add_argument(
        "--project-id",
        type=str,
        help="the project_id in spacemake project_df.csv",
        required=True,
    )
    parser.add_argument(
        "--sample-id",
        type=str,
        help="the sample_id in spacemake project_df.csv",
        required=True,
    )
    parser.add_argument(
        "--puck-barcode-file-id-qc",
        type=str,
        help="a path to the puck_barcode_file_id_qc",
        required=True,
    )
    # These have the paths to the input files used for generating plots
    parser.add_argument(
        "--dge-summary-paths",
        type=str,
        nargs="+",
        help="paths to the dge summary files, must be in same number and order as --run-modes",
        required=True,
    )
    parser.add_argument(
        "--run-modes",
        type=str,
        nargs="+",
        help="run modes of the sample for which the report will be generated",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--complete-data-root",
        type=str,
        help="path to where the sample data is stored",
        required=True,
    )
    parser.add_argument(
        "--split-reads-read-type",
        type=str,
        nargs="+",
        help="path to the 'read_type_num' file(s) generated by read annotator. One per mapping strategy.",
        required=True,
    )
    # These additionally configure the plotting functionality
    parser.add_argument(
        "--is-spatial",
        type=str,
        help="Whether the current sample is spatial or not",
        required=True,
        choices=["True", "False"],
        default="False",
    )
    # This specifies where the output file will be generated
    parser.add_argument(
        "--out-html-report",
        type=str,
        help="where the HTML report will be saved",
        required=True,
    )

    return parser


def reverse_readline(filename, buf_size=8192):
    """A generator that returns the lines of a file in reverse order"""
    with open(filename, "rb") as fh:
        segment = None
        offset = 0
        fh.seek(0, os.SEEK_END)
        file_size = remaining_size = fh.tell()
        while remaining_size > 0:
            offset = min(file_size, offset + buf_size)
            fh.seek(file_size - offset)
            buffer = fh.read(min(remaining_size, buf_size)).decode(encoding="utf-8")
            remaining_size -= buf_size
            lines = buffer.split("\n")
            # The first line of the buffer is probably not a complete line so
            # we'll save it and append it to the last line of the next buffer
            # we read
            if segment is not None:
                # If the previous chunk starts right from the beginning of line
                # do not concat the segment to the last line of new chunk.
                # Instead, yield the segment first
                if buffer[-1] != "\n":
                    lines[-1] += segment
                else:
                    yield segment
            segment = lines[0]
            for index in range(len(lines) - 1, 0, -1):
                if lines[index]:
                    yield lines[index]
        # Don't yield None if the file was empty
        if segment is not None:
            yield segment


def read_star_log_file(log_file):
    file = os.path.join(log_file)

    if not os.path.isfile(file):
        return (1, 1)

    star_stats = {
        "input_reads": 0,
        "uniq_mapped_reads": 0,
        "multi_mapped_reads": 0,
        "too_many_mapped_reads": 0,
        "too_many_mapped_reads": 0,
        "unmapped_too_short": 0,
        "unmapped_other": 0,
        "chimeric": 0,
    }

    log_name_stat = {
        "Number of input reads": "input_reads",
        "Uniquely mapped reads number": "uniq_mapped_reads",
        "Number of reads mapped to multiple loci": "multi_mapped_reads",
        "Number of reads mapped to too many loci": "too_many_mapped_reads",
        "Number of reads unmapped: too many mismatches": "unmapped_mismatches",
        "Number of reads unmapped: too short": "unmapped_too_short",
        "Number of reads unmapped: other": "unmapped_other",
        "Number of chimeric reads": "chimeric",
        "Average mapped length": "avg_mapped_length",
    }

    with open(file) as fi:
        idx = 0
        for line in fi:
            _log_id = line.strip().split("|")[0].strip()
            if _log_id in log_name_stat.keys():
                if _log_id == "Average mapped length":
                    star_stats[log_name_stat[_log_id]] = line.strip().split("|")[1]
                else:
                    star_stats[log_name_stat[_log_id]] = round(int(line.strip().split("|")[1]) / 1e6, 2)
                # Do not convert to millions the Average mapped length

            idx += 1

    return star_stats


def read_split_reads_file(split_reads_file):
    file = os.path.join(split_reads_file)

    read_types = dict()
    with open(file) as fi:
        for line in fi:
            line = line.strip().split(" ")
            read_types[line[0]] = round(int(line[1]) / 1e6, 2)

    return read_types


def read_bowtie_log_file(log_file):
    file = os.path.join(log_file)
    bowtie_stats = {
        "input_reads": 0,
        "unique_aligned": 0,
        "multimapper": 0,
        "unaligned": 0,
    }
    log_line_stat = {
        5: "input_reads",
        3: "unaligned",
        2: "unique_aligned",
        1: "multimapper",
    }
    max_line = max(log_line_stat.keys())

    idx = 0
    for line in reverse_readline(file):
        if idx in log_line_stat.keys():
            bowtie_stats[log_line_stat[idx]] = round(int(line.strip().split(" ")[0]) / 1e6, 2)
        idx += 1
        if max_line < idx:
            break

    return bowtie_stats


def plot_metric(values, axis, nbins=100, color="#000000"):
    # decide linear or logarithmic scale
    min_difference = values.max() - values.min()

    hist, bins = np.histogram(values, bins=nbins)
    if np.abs(min_difference) < 100:
        axis.bar(bins[:-1], hist, color=color)

    else:
        logbins = np.logspace(np.log10(bins[0] + 1), np.log10(bins[-1]), nbins)
        axis.hist(values, bins=logbins, color=color)
        axis.set_xscale("log")

    axis.spines[["right", "top"]].set_visible(False)


def plot_umi_cutoff(obs_df):
    umi_cutoffs = np.arange(10, 20000, 10)

    def summarise_dge_summary(df, umi_cutoff):
        df_filter = df[df["total_counts"] > umi_cutoff]

        df_summary = df_filter[
            [
                "n_reads",
                "total_counts",
                "n_genes_by_counts",
                "reads_per_counts",
            ]
        ].median()
        df_summary["n_beads"] = len(df_filter)

        return df_summary

    umi_cutoff_data = pd.DataFrame(
        np.vstack([summarise_dge_summary(obs_df, umi_cutoff).values for umi_cutoff in umi_cutoffs]),
        columns=[
            "n_reads",
            "total_counts",
            "n_genes_by_counts",
            "reads_per_counts",
            "n_beads",
        ],
    )
    umi_cutoff_data["umi_cutoffs"] = umi_cutoffs

    fig, axes = plt.subplots(2, 3, figsize=(8, 4))
    axes[0, 0].plot(
        umi_cutoff_data["umi_cutoffs"],
        umi_cutoff_data["n_beads"],
        color="black",
        linewidth=1,
    )
    axes[0, 0].set_ylabel("number of\nspatial units")
    axes[0, 0].set_xlabel("minimum UMI")
    axes[0, 0].set_yscale("log")
    axes[0, 0].set_xscale("log")
    axes[0, 0].spines[["right", "top"]].set_visible(False)

    axes[0, 1].plot(
        umi_cutoff_data["umi_cutoffs"],
        umi_cutoff_data["n_reads"],
        color=clrs["reads"],
        linewidth=1,
    )
    axes[0, 1].set_ylabel("median reads\nper spatial unit")
    axes[0, 1].set_xlabel("minimum UMI")
    axes[0, 1].set_xscale("log")
    axes[0, 1].spines[["right", "top"]].set_visible(False)

    axes[0, 2].plot(
        umi_cutoff_data["umi_cutoffs"],
        umi_cutoff_data["n_genes_by_counts"],
        color=clrs["genes"],
        linewidth=1,
    )
    axes[0, 2].set_ylabel("median genes\nper spatial unit")
    axes[0, 2].set_xlabel("minimum UMI")
    axes[0, 2].set_xscale("log")
    axes[0, 2].spines[["right", "top"]].set_visible(False)

    axes[1, 0].plot(
        umi_cutoff_data["umi_cutoffs"],
        umi_cutoff_data["total_counts"],
        color=clrs["umis"],
        linewidth=1,
    )
    axes[1, 0].set_ylabel("median UMIs\nper spatial unit")
    axes[1, 0].set_xlabel("minimum UMI")
    axes[1, 0].set_xscale("log")
    axes[1, 0].spines[["right", "top"]].set_visible(False)

    axes[1, 1].plot(
        umi_cutoff_data["umi_cutoffs"],
        umi_cutoff_data["reads_per_counts"],
        color=clrs["pcr"],
        linewidth=1,
    )
    axes[1, 1].set_ylabel("median reads/UMI\nper spatial unit")
    axes[1, 1].set_xlabel("minimum UMI")
    axes[1, 1].set_xscale("log")
    axes[1, 1].spines[["right", "top"]].set_visible(False)
    axes[1, 2].axis("off")

    plt.tight_layout()
    return fig, axes


def plot_histogram_beads(obs_df):
    # One for each run mode
    fig, axes = plt.subplots(2, 2, figsize=(7, 3.5))
    plot_metric(obs_df["n_reads"], axes[0, 0], 100, clrs["reads"])
    axes[0, 0].set_ylabel("# of\nspatial units")
    axes[0, 0].set_xlabel("# of reads")

    reads_per_counts = obs_df["reads_per_counts"]
    reads_per_counts = np.nan_to_num(reads_per_counts)
    plot_metric(reads_per_counts, axes[0, 1], 100, clrs["pcr"])
    axes[0, 1].set_ylabel("# of\nspatial units")
    axes[0, 1].set_xlabel("# of reads/UMI")

    plot_metric(obs_df["n_genes_by_counts"], axes[1, 0], 100, clrs["genes"])
    axes[1, 0].set_ylabel("# of\nspatial units")
    axes[1, 0].set_xlabel("# of genes")

    plot_metric(obs_df["total_counts"], axes[1, 1], 100, clrs["umis"])
    axes[1, 1].set_ylabel("# of\nspatial units")
    axes[1, 1].set_xlabel("# of UMIs")

    plt.tight_layout()
    return fig, axes


def plot_nucleotide_distribution_beads(dge_summary):
    dge_summary["reads_cumsum"] = dge_summary["n_reads"].cumsum()
    dge_summary["quartile"] = pd.cut(
        dge_summary["reads_cumsum"],
        bins=4,
        include_lowest=True,
        labels=["Q1", "Q2", "Q3", "Q4"],
    )
    dge_summary.drop(columns=["reads_cumsum"], inplace=True)

    # Create a dataframe to show the count of nucleotides/barcode
    cell_bc_len = len(dge_summary["cell_bc"].iloc[0])
    nucls = dge_summary["cell_bc"].str.strip().apply(list).apply(pd.Series)
    nucls = pd.concat([dge_summary[["cell_bc", "quartile"]], nucls], axis=1)
    nucls = nucls.melt(id_vars=["cell_bc", "quartile"], var_name="pos", value_name="nucl")
    nucls = nucls.groupby(["pos", "nucl", "quartile"]).size().reset_index(name="nucl_count")
    nucls = nucls.pivot_table(
        index=["pos", "nucl"], columns="quartile", values="nucl_count", fill_value=0
    ).reset_index()
    lbl_df = dge_summary.groupby("quartile").size().reset_index(name="lbl")
    lbl_df["lbl"] = lbl_df.apply(lambda row: f"{row['quartile']} (n={row['lbl']})", axis=1)
    lbls = dict(zip(lbl_df["quartile"], lbl_df["lbl"]))

    # Create the plot
    fig, axes = plt.subplots(2, 2, figsize=(8, 4))
    x = np.arange(1, cell_bc_len + 1)  # the label locations

    for name, group in nucls.groupby("nucl"):
        axes[0, 0].plot(x, group["Q1"], color=nucl_clrs[name], linewidth=2, label=name)
        axes[0, 0].set_xlim(0.1, cell_bc_len + 1.5)
        axes[0, 0].set_xticks([])
        axes[0, 0].set_title(lbls["Q1"])
        axes[0, 0].spines[["right", "top", "bottom"]].set_visible(False)

        axes[0, 1].plot(x, group["Q2"], color=nucl_clrs[name], linewidth=2)
        axes[0, 1].set_xlim(0.1, cell_bc_len + 1.5)
        axes[0, 1].set_xticks([])
        axes[0, 1].set_title(lbls["Q2"])
        axes[0, 1].spines[["right", "top", "bottom"]].set_visible(False)

        axes[1, 0].plot(x, group["Q3"], color=nucl_clrs[name], linewidth=2)
        axes[1, 0].set_xlim(0.1, cell_bc_len + 1.5)
        axes[1, 0].set_title(lbls["Q3"])
        axes[1, 0].spines[["right", "top"]].set_visible(False)
        axes[1, 0].set_xticks(list(set(list(range(1, cell_bc_len + 1, 2)) + [cell_bc_len])))

        axes[1, 1].plot(x, group["Q4"], color=nucl_clrs[name], linewidth=2)
        axes[1, 1].set_xlim(0.1, cell_bc_len + 1.5)
        axes[1, 1].set_title(lbls["Q4"])
        axes[1, 1].spines[["right", "top"]].set_visible(False)
        axes[1, 1].set_xticks(list(set(list(range(1, cell_bc_len + 1, 2)) + [cell_bc_len])))

    handles, labels = axes[0, 0].get_legend_handles_labels()
    legend = fig.legend(
        handles,
        labels,
        loc="center right",
        bbox_to_anchor=(1.1, 0.5),
        borderaxespad=0,
        title="Nucleotide",
    )
    legend.set_frame_on(False)
    fig.text(0.5, 0.0, "nucleotide position in the barcode", ha="center")
    plt.tight_layout()

    return fig, axes


def plot_entropy_compression(obs_df, nbins=30):
    fig, axes = plt.subplots(2, 1, figsize=(7, 4))
    axes[0].hist(
        obs_df["theoretical_entropy"].values,
        color=cpalette["grey"],
        bins=nbins,
        edgecolor="black",
        label="Theoretical",
    )
    axes[0].hist(
        obs_df["exact_entropy"].values,
        color=cpalette["orange"],
        bins=nbins,
        edgecolor="black",
        alpha=0.7,
        label="Observed",
    )
    axes[0].set_xlabel("Shannon entropy of barcodes")
    axes[0].set_ylabel("# of barcodes")
    axes[0].spines[["right", "top"]].set_visible(False)
    legend0 = axes[0].legend(loc="upper left")
    legend0.set_frame_on(False)

    axes[1].hist(
        obs_df["theoretical_compression"].values,
        color=cpalette["grey"],
        bins=nbins,
        edgecolor="black",
        label="Theoretical",
    )
    axes[1].hist(
        obs_df["exact_compression"].values,
        color=cpalette["orange"],
        bins=nbins,
        edgecolor="black",
        alpha=0.7,
        label="Observed",
    )
    axes[1].set_xlabel("Length of barcodes after compression")
    axes[1].set_ylabel("# of barcodes")
    axes[1].spines[["right", "top"]].set_visible(False)
    axes[1].set_xlim(left=0)
    return fig, axes


def plot_spatial_qc_metric(
    adata,
    metric="total_counts",
    puck_bead_size=1,
    px_by_um=1,
    x_breaks=None,
    y_breaks=None,
    x_mm_breaks=None,
    y_mm_breaks=None,
):
    fig, axes = plt.subplots(1, 1, figsize=(5, 5))
    sc.pl.spatial(
        adata,
        img_key=None,
        size=puck_bead_size * 1.5,
        spot_size=px_by_um,
        color=metric,
        ax=axes,
        title=SPATIAL_METRICS_TITLES[metric],
        show=False,
        vmax=np.quantile(adata.obs[metric], 0.9),
        cmap="magma",
    )
    axes.spines[["right", "top"]].set_visible(False)
    axes.set_xticks(x_breaks)
    axes.set_xticklabels(x_mm_breaks)
    axes.set_yticks(y_breaks)
    axes.set_yticklabels(y_mm_breaks)
    axes.set_ylabel("")
    axes.set_xlabel("")

    return fig, axes


def plot_to_base64(fig):
    my_stringIObytes = io.BytesIO()
    fig.savefig(my_stringIObytes, format="jpg", dpi=300)
    plt.close()
    my_stringIObytes.seek(0)
    return base64.b64encode(my_stringIObytes.read()).decode()


def generate_table_mapping_statistics(complete_data_root: str, split_reads_read_type: str):
    # Initialize empty lists to store the filenames
    bowtie_log_files = []
    star_log_files = []

    # Iterate over the files in the folder
    for filename in os.listdir(complete_data_root):
        # Check if the file ends with .bam.log
        if filename.endswith(".bam.log") and "final" not in filename:
            bowtie_log_files.append(filename)
        # Check if the file ends with .Log.final.out
        elif filename.endswith(".Log.final.out") and filename != "star.Log.final.out":
            star_log_files.append(filename)

    bowtie_logs = []
    for bowtie_log_file in bowtie_log_files:
        bowtie_log = read_bowtie_log_file(os.path.join(complete_data_root, bowtie_log_file))
        bowtie_log["name"] = bowtie_log_file.split(".")[0]
        bowtie_log["mapper"] = "bowtie2"
        bowtie_logs.append(bowtie_log)

    star_logs = []
    for star_log_file in star_log_files:
        star_log = read_star_log_file(os.path.join(complete_data_root, star_log_file))
        star_log["name"] = star_log_file.split(".")[1]
        star_log["mapper"] = "STAR"
        star_logs.append(star_log)

    # we sort the mapping statistics by the number of input reads, merge into a single list
    all_logs = star_logs + bowtie_logs

    idx_log = np.argsort([log["input_reads"] for log in all_logs])[::-1]
    all_logs = [all_logs[idx] for idx in idx_log]

    reads_type = read_split_reads_file(split_reads_read_type)
    reads_type["name"] = "reads_type"
    all_logs += [reads_type]

    return all_logs


def load_dge_summary(obs_df_path, with_adata=True):
    obs_df = pd.read_csv(obs_df_path)
    obs_df['cell_bc'] = obs_df['cell_bc'].astype(str)
    obs_df = obs_df[obs_df['cell_bc'].str.contains('^[0-9]+|^[ACTGAN]+$', regex=True)]
    empty_ad = csr_matrix((len(obs_df), 1), dtype=int)
    adata = ad.AnnData(empty_ad)
    adata.obs = obs_df
    if with_adata:
        return obs_df, adata
    else:
        return obs_df


def generate_qc_sequencing_metadata(
    project_id: str,
    sample_id: str,
    dge_summary_paths: Union[list, dict],  # from snakemake_helper_funtions.get_qc_sheet_input_files
    complete_data_root: str,
    split_reads_read_type: Union[str, list],
    puck_barcode_file_id_qc: str,
    is_spatial: bool = False,
    run_modes: list = None,
):
    if isinstance(dge_summary_paths, str):
        dge_summary_paths = [dge_summary_paths]
    if isinstance(run_modes, str):
        run_modes = [run_modes]
    if (
        isinstance(run_modes, list)
        and isinstance(dge_summary_paths, list)
        and len(run_modes) == len(dge_summary_paths)
    ):
        dge_summary_paths = {
            f'{run_mode}.dge_summary': dge_summary_path for run_mode, dge_summary_path in zip(run_modes, dge_summary_paths)
        }
    elif (
        isinstance(run_modes, list)
        and isinstance(dge_summary_paths, list)
        and len(run_modes) != len(dge_summary_paths)
    ):
        raise ValueError("'run_modes' and 'dge_summary_paths' must have the same length")

    report = {
        "type": "qc_report",
        "sampleinfo": [],
        "runmodes": [],
        "mappingstats": [],
        "summarisedmetrics": [],
        "is_spatial": is_spatial,
        "plots": [],
    }
    main_plots = {
        "kneeplot": [],
        "umicutoff": [],
        "histogrambeads": [],
        "nucleotidedistribution": [],
        "shannonentropy": [],
        "spatialqc": [],
    }
    report["plots"] = main_plots

    # Loading project_df for metadata
    config = ConfigFile.from_yaml("config.yaml")
    project_df = ProjectDF("project_df.csv", config=config)

    # Table: sample info
    sample_info = project_df.get_sample_info(project_id, sample_id)
    report["sampleinfo"] = {}
    report["sampleinfo"]["project_id"] = project_id
    report["sampleinfo"]["sample_id"] = sample_id
    report["sampleinfo"]["puck_barcode_file_id"] = puck_barcode_file_id_qc
    report["sampleinfo"].update({svar: sample_info[svar] for svar in SAMPLEINFO_VARS})

    # Table: run modes
    if run_modes is None:
        run_modes = sample_info["run_mode"]

    for run_mode in run_modes:
        run_mode_info = {}
        run_mode_info["variables"] = project_df.config.get_run_mode(run_mode).variables
        run_mode_info["name"] = run_mode
        report["runmodes"].append(run_mode_info)

    # Table: mapping statistics
    if isinstance(split_reads_read_type, str):
        split_reads_read_type = [split_reads_read_type]

    for s in split_reads_read_type:
        # assumes that the directory of s is the name of the mapping strategy
        mappingstat = {
            "name": os.path.basename(os.path.dirname(s)),
            "all_stats": generate_table_mapping_statistics(complete_data_root, s),
        }
        report["mappingstats"].append(mappingstat)

    # Loading all dge data summaries
    # Table: summarised metrics over beads
    dge_summaries = {}
    for run_mode in run_modes:
        dge_summaries[run_mode] = {}
        summarised_metrics = {"name": run_mode, "variables": None}
        _dge_summary = load_dge_summary(dge_summary_paths[f'{run_mode}.dge_summary'])
        dge_summaries[run_mode]["df"] = _dge_summary[0]
        dge_summaries[run_mode]["adata"] = _dge_summary[1]

        obs_df = dge_summaries[run_mode]["df"]
        metrics_beads = obs_df[["n_genes_by_counts", "reads_per_counts", "n_reads", "total_counts"]].median().to_dict()
        metrics_beads["n_beads"] = len(obs_df)
        metrics_beads["sum_reads"] = f'{round(obs_df["n_reads"].sum()/1e6, 2)} (1e6)'
        summarised_metrics["variables"] = metrics_beads
        report["summarisedmetrics"].append(summarised_metrics)

    # Plots
    # 'Knee'-plot
    for run_mode, dge_summary in dge_summaries.items():
        kneeplot = {"name": run_mode, "plot": None}
        obs_df = dge_summary["df"]
        fig, axis = plt.subplots(1, 1, figsize=(5, 3))
        axis.plot(
            np.arange(len(obs_df)),
            np.cumsum(obs_df["n_reads"].values),
            color="black",
            linewidth=1,
        )
        axis.set_ylabel("Cumulative\nsum of reads")
        axis.set_xlabel("Beads sorted by number of reads")
        axis.spines[["right", "top"]].set_visible(False)
        plt.tight_layout()
        kneeplot["plot"] = plot_to_base64(fig)
        report["plots"]["kneeplot"].append(kneeplot)

    # UMI cutoff plots
    for run_mode, dge_summary in dge_summaries.items():
        umicutoff = {"name": run_mode, "plot": None}
        obs_df = dge_summary["df"]
        fig, axes = plot_umi_cutoff(obs_df)
        umicutoff["plot"] = plot_to_base64(fig)
        report["plots"]["umicutoff"].append(umicutoff)

    # Histogram beads
    for run_mode, dge_summary in dge_summaries.items():
        histogrambeads = {"name": run_mode, "plot": None}
        obs_df = dge_summary["df"]
        fig, axes = plot_histogram_beads(obs_df)
        histogrambeads["plot"] = plot_to_base64(fig)
        report["plots"]["histogrambeads"].append(histogrambeads)

    # Check if the run_mode is meshed

    # Nucleotide distribution per beads
    for run_mode, dge_summary in dge_summaries.items():
        if not project_df.config.get_run_mode(run_mode).variables["mesh_data"]:
            nucleotidedistribution = {"name": run_mode, "plot": None}
            obs_df = dge_summary["df"]
            fig, axes = plot_nucleotide_distribution_beads(obs_df)
            nucleotidedistribution["plot"] = plot_to_base64(fig)
            report["plots"]["nucleotidedistribution"].append(nucleotidedistribution)

    # Shannon entropy
    for run_mode, dge_summary in dge_summaries.items():
        shannonentropy = {"name": run_mode, "plot": None}
        obs_df = dge_summary["df"]
        fig, axes = plot_entropy_compression(obs_df)
        shannonentropy["plot"] = plot_to_base64(fig)
        report["plots"]["shannonentropy"].append(shannonentropy)

    # Return here if not spatial
    if not is_spatial:
        return report

    # Proceed with Spatial QC plots
    # Variables for spatial
    px_by_um, puck_bead_size = 1, 1
    x_breaks, y_breaks = None, None
    x_mm_breaks, y_mm_breaks = None, None
    puck_width_um = 1

    for run_mode, dge_summary in dge_summaries.items():
        # Loading adata for scanpy plotting
        adata = dge_summary["adata"]
        adata.obsm["spatial"] = adata.obs[["x_pos", "y_pos"]].values
        report["n_cells"] = len(adata)

        # Loading metadata from spatial pucks and run mode
        puck_metrics = (
            project_df.get_puck_barcode_file_metrics(
                project_id=project_id,
                sample_id=sample_id,
                puck_barcode_file_id=puck_barcode_file_id_qc,
            ),
        )[0]

        puck_settings = project_df.get_puck_variables(project_id, sample_id, return_empty=True)

        run_mode_vars = project_df.config.get_run_mode(run_mode).variables

        px_by_um = puck_metrics["px_by_um"]
        mesh_spot_diameter_um = run_mode_vars["mesh_spot_diameter_um"]
        meshed = run_mode_vars["mesh_data"]
        spot_diameter_um = puck_settings["spot_diameter_um"]

        # Set limits and axes units for the spatial plots
        x_limits = adata.obsm["spatial"][:, 0].min(), adata.obsm["spatial"][:, 0].max()
        y_limits = adata.obsm["spatial"][:, 1].min(), adata.obsm["spatial"][:, 1].max()
        puck_width_um = (x_limits[1] - x_limits[0]) / px_by_um

        ratio = (x_limits[1] - x_limits[0]) / (y_limits[1] - y_limits[0])

        scale_factor = 2 if puck_width_um < 3000 else 3
        mm_dist = max(10**scale_factor, round(puck_width_um / 3, scale_factor))
        mm_diff = mm_dist / 1000

        def_plot_bead_size = 0.5 if report["n_cells"] > 5000 else 0.75
        def_plot_bead_size = 0.1 if report["n_cells"] > 10000 else def_plot_bead_size
        def_plot_bead_size = 0.05 if report["n_cells"] > 25000 else def_plot_bead_size

        puck_bead_size = max(def_plot_bead_size, mesh_spot_diameter_um if meshed else spot_diameter_um)
        x_mm_breaks = np.arange(0, puck_width_um, mm_dist)
        x_mm_breaks = [f"{round(i, 1)} mm" for i in x_mm_breaks * mm_diff / mm_dist]
        y_mm_breaks = np.arange(0, puck_width_um / ratio, mm_dist)
        y_mm_breaks = [f"{round(i, 1)} mm" for i in y_mm_breaks * mm_diff / mm_dist]

        x_breaks = np.arange(x_limits[0], x_limits[1], px_by_um * mm_dist)
        y_breaks = np.arange(y_limits[0], y_limits[1], px_by_um * mm_dist)

        # Plot spatial metrics
        spatialqc = {"name": run_mode, "plots": []}
        for spatial_metric in SPATIAL_METRICS:
            if spatial_metric not in adata.obs.columns:
                continue
            fig, axes = plot_spatial_qc_metric(
                adata,
                spatial_metric,
                puck_bead_size=puck_bead_size,
                px_by_um=px_by_um,
                x_breaks=x_breaks,
                y_breaks=y_breaks,
                x_mm_breaks=x_mm_breaks,
                y_mm_breaks=y_mm_breaks,
            )
            spatialqc["plots"].append(plot_to_base64(fig))
        report["plots"]["spatialqc"].append(spatialqc)

    return report


def generate_html_report(data, template_file):
    with open(template_file, "r") as template_data:
        template_content = template_data.read()
        template = Template(template_content)

    html_report = template.render(report=data)

    return html_report


@message_aggregation(logger_name)
def cmdline():
    """cmdline."""
    import argparse

    parser = argparse.ArgumentParser(
        allow_abbrev=False,
        description="generate spacemake's 'QC sequencing' with python",
    )
    parser = setup_parser(parser)

    args = parser.parse_args()
    template_file = os.path.join(absolute_path, "templates/qc_sequencing.html")

    report_metadata = generate_qc_sequencing_metadata(
        args.project_id,
        args.sample_id,
        args.dge_summary_paths,
        args.complete_data_root,
        args.split_reads_read_type,
        args.puck_barcode_file_id_qc,
        STRTOBOOL[args.is_spatial],
        args.run_modes,
    )

    html_report = generate_html_report(report_metadata, template_file)

    with open(args.out_html_report, "w") as output:
        output.write(html_report)


if __name__ == "__main__":
    cmdline()
