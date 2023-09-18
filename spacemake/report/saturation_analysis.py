import base64
import io
import logging
import os
from typing import Union

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from itertools import cycle
from jinja2 import Template
from scipy.stats import gaussian_kde
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

SAMPLEINFO_VARS = [
    "species",
    "sequencing_date",
    "investigator",
    "experiment",
    "barcode_flavor",
    "sequencing_date",
    "puck"
]

parula_dict = {
    1: "#352a87",
    2: "#2058b0",
    3: "#1f7eb8",
    4: "#28a7d7",
    5: "#38d7e3",
    6: "#99d4d0",
    7: "#aacca1",
    8: "#bbcc74",
    9: "#cbcc49",
    10: "#e0d317"
}

PCT_DOWNSAMPLE_TO_PLOT = [20, 40, 60, 80, 100]
DOWNSAMPLE_PCTS = list(range(10,110,10))

# Run example:
# python /home/dleonpe/spacemake_project/repos/spacemake/spacemake/report/saturation_analysis.py \
#     --project-id fc_sts_76 \
#     --sample-id fc_sts_76_3 \
#     --run-modes fc_sts_novaseq_SP_mesh_7um fc_sts_novaseq_SP_mesh_7um fc_sts_novaseq_SP_mesh_7um fc_sts_novaseq_SP_mesh_7um fc_sts_novaseq_SP_mesh_7um fc_sts_novaseq_SP_mesh_7um fc_sts_novaseq_SP_mesh_7um fc_sts_novaseq_SP_mesh_7um fc_sts_novaseq_SP_mesh_7um fc_sts_novaseq_SP_mesh_7um \
#     --downsampled-dge-summary /data/rajewsky/home/dleonpe/projects/openst_paper/data/1_spacemake_mouse_new/projects/fc_sts_76/processed_data/fc_sts_76_3/illumina/downsampled_data/*/dge/dge.all.polyA_adapter_trimmed.mm_included.spatial_beads.mesh_7_hexagon_fc_010_L3_tile_2157.obs.csv /home/dleonpe/spacemake_project/data/1_spacemake_mouse_new/projects/fc_sts_76/processed_data/fc_sts_76_3/illumina/complete_data/dge/dge.all.polyA_adapter_trimmed.mm_included.spatial_beads.mesh_7_hexagon_fc_010_L3_tile_2157.obs.csv \
#     --out-html-report ~/Desktop/fc_sts_76_3_saturation_analysis.html \
#     --puck-barcode-file-id fc_010_L3_tile_2157

# .deciledmedian has the 'parula' color map

logger_name = "spacemake.report.saturation_analysis"
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
        "--run-modes",
        type=str,
        nargs="+",
        help="run modes of the sample for which the report will be generated",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--downsampled-dge-summary",
        type=str,
        nargs="+",
        help="path to the 'downsampled_dge_summary' file(s) generated by spacemake downsampling analysis.",
        required=True,
    )
    parser.add_argument(
        "--puck-barcode-file-id",
        type=str,
        help="the puck_barcode_file_id for the current report",
        required=True,
    )
    # This specifies where the output file will be generated
    parser.add_argument(
        "--out-html-report",
        type=str,
        help="where the HTML report will be saved",
        required=True,
    )

    return parser


def plot_density_metric_faceted(values, metric, log_scale=True, color='#000000', title=''):
    fig, axes = plt.subplots(len(PCT_DOWNSAMPLE_TO_PLOT), 1, figsize=(5, 0.5*len(PCT_DOWNSAMPLE_TO_PLOT)))

    i = 0
    for downsample_pct, value_density in values.groupby("_downsample_pct_report"):
        if int(downsample_pct) in PCT_DOWNSAMPLE_TO_PLOT:
            density_function = gaussian_kde(np.nan_to_num(value_density[metric]), bw_method=0.1)
            x = np.linspace(1, max(np.nan_to_num(values[metric])), 100)
            
            axes[i].plot(x, density_function(x), color='black', linewidth=1)
            axes[i].fill_between(x, density_function(x), color=color)
            axes[i].set_yticks([])

            if log_scale:
                axes[i].set_xscale("log")
            
            axes[i].spines[["right", "top", "bottom"]].set_visible(False)
            axes[i].text(1.05, 0.5, f'{downsample_pct}%', transform=axes[i].transAxes, va='center')
            i += 1

        axes[-1].spines[["right", "top"]].set_visible(False)
        axes[-1].spines[["left", "bottom"]].set_visible(True)
        axes[-1].set_xlabel(title)

    for i in range(i-1):
        axes[i].set_xticks([])

    fig.text(0.0, 0.6, 'density', va='center', rotation='vertical')
    plt.tight_layout()

    return fig, axes


def plot_median_per_run_mode(values, metric, umi_cutoffs, color='#000000', title=''):
    fig, axes = plt.subplots(1, 1, figsize=(5, 3))

    lines = ["-","--","-.",":"]
    linecycler = cycle(lines)
    handles, labels = [], []

    for umi_cutoff in umi_cutoffs:
        _values = values[values['total_counts'] > umi_cutoff]
        median_values = _values[[metric, '_downsample_pct_report']].groupby('_downsample_pct_report').median().reset_index()

        linestyle = next(linecycler)
    
        line, = axes.plot(median_values['_downsample_pct_report'], median_values[metric], linestyle, color=color, label=umi_cutoff)
        axes.scatter(median_values['_downsample_pct_report'], median_values[metric], s=20, color=color, edgecolors='black')

        handles.append(line)
        labels.append(umi_cutoff)

    axes.set_xticks(PCT_DOWNSAMPLE_TO_PLOT)
    axes.set_xticklabels([f'{pct}%'for pct in PCT_DOWNSAMPLE_TO_PLOT])
    axes.spines[["right", "top"]].set_visible(False)
    axes.set_xlabel("downsampling percentage")
    axes.set_ylabel(title)

    legend = axes.legend(handles, labels, loc='lower right', title="UMI cutoff")
    legend.set_frame_on(False)

    plt.tight_layout()
    return fig, axes


def generate_deciled_data(values):
    # Group by '_downsample_pct_report' and perform the necessary calculations
    def calculate_deciles(group):
        group['cumsum_reads'] = group['n_reads'].cumsum()
        group['decile_limit'] = group['n_reads'].sum() / 10
        group['decile'] = (group['cumsum_reads'] / group['decile_limit']).floordiv(1) + 1
        return group.loc[group['decile'] < 11]

    # Group by '_downsample_pct_report' and apply the calculate_deciles function
    decile_dat = values.groupby('_downsample_pct_report').apply(calculate_deciles).reset_index(drop=True)

    # Group by 'percentage' and 'decile' and calculate medians and counts
    decile_dat = (decile_dat.groupby(['_downsample_pct_report', 'decile'])
                .agg({'n_reads': 'median', 'n_genes_by_counts': 'median', 'reads_per_counts': 'median', 'total_counts': 'median', 'cell_bc': 'count'})
                .reset_index())

    # Melt the DataFrame to long format
    decile_dat = pd.melt(decile_dat, id_vars=['_downsample_pct_report', 'decile'], var_name='observation', value_name='value')

    # Convert 'decile' and '_downsample_pct_report' to appropriate data types
    decile_dat['decile'] = decile_dat['decile'].astype('category')
    decile_dat['_downsample_pct_report'] = decile_dat['_downsample_pct_report'].astype(int)

    mapping_dict = {'n_reads': 'median_reads', 'n_genes_by_counts': 'median_genes', 'reads_per_counts': 'median_pcr', 'total_counts': 'median_umis', 'cell_bc': 'n_beads'}
    decile_dat['observation'] = decile_dat['observation'].replace(mapping_dict)

    return decile_dat


def plot_deciled_median(decile_dat):
    fig, axes = plt.subplots(3, 2, figsize=(6, 4))

    # Iterate through each unique 'observation' for facetting
    for i, (obs, data) in enumerate(decile_dat.groupby('observation')):
        for _obs, _data in data.groupby('decile'):
            axes.flatten()[i].plot(_data['_downsample_pct_report'], _data['value'], label=_obs, linewidth=0.6, color=parula_dict[_obs])
            axes.flatten()[i].scatter(_data['_downsample_pct_report'], _data['value'], s=20, edgecolors='black', color=parula_dict[_obs])

        axes.flatten()[i].set_xticks([0, 20, 40, 60, 80, 100])
        axes.flatten()[i].set_xticklabels(['0', '20', '40', '60', '80', '100'])
        axes.flatten()[i].set_title(obs)
        axes.flatten()[i].spines[['top', 'right']].set_visible(False)

    axes.flatten()[i].set_xlabel("downsampling percentage")
    if i % 2 == 0:
        axes.flatten()[-1].axis("off")

    # Create a single legend at the bottom
    handles, labels = [], []
    for obs in parula_dict:
        handles.append(plt.Line2D([0], [0], marker='o', color='w', markerfacecolor=parula_dict[obs], markersize=8))
        labels.append(str(obs))

    fig.legend(handles, labels, title='Decile', loc='lower right', ncol=3, bbox_to_anchor=(0.95, 0.02))

    plt.tight_layout()

    return fig, axes


def plot_to_base64(fig):
    my_stringIObytes = io.BytesIO()
    fig.savefig(my_stringIObytes, format="jpg", dpi=300)
    plt.close()
    my_stringIObytes.seek(0)
    return base64.b64encode(my_stringIObytes.read()).decode()


def load_dge_summary_downsampling(downsampled_dge_summary, run_mode, puck_barcode_file_id):
    obs_df = pd.DataFrame()
    for downsample_pct in DOWNSAMPLE_PCTS:
        _obs_df = pd.read_csv(downsampled_dge_summary[f'downsampled_dge_summary.{run_mode}.{downsample_pct}.{puck_barcode_file_id}'])
        _obs_df['_downsample_pct_report'] = downsample_pct
        obs_df = pd.concat([obs_df, _obs_df])

    return obs_df


def generate_saturation_analysis_metadata(
    project_id: str,
    sample_id: str,
    run_modes: Union[list, str],
    downsampled_dge_summary: Union[dict, list, str],
    puck_barcode_file_id: str,
):
    if isinstance(downsampled_dge_summary, str):
        downsampled_dge_summary = [downsampled_dge_summary]
    if isinstance(run_modes, str):
        run_modes = [run_modes]
    if (
        isinstance(run_modes, list)
        and isinstance(downsampled_dge_summary, list)
        and len(run_modes) == len(downsampled_dge_summary)
    ):
        downsampled_dge_summary = {
            f'downsampled_dge_summary.{run_mode}.{d_pct}.{puck_barcode_file_id}': d for run_mode, d, d_pct in zip(run_modes, downsampled_dge_summary, DOWNSAMPLE_PCTS)
        }
    elif (
        isinstance(run_modes, list)
        and isinstance(downsampled_dge_summary, list)
        and len(run_modes) != len(downsampled_dge_summary)
    ):
        raise ValueError("'run_modes' and 'downsampled_dge_summary' must have the same length")

    report = {
        "type": "saturation_analysis",
        "runinformation": [],
        "date": None,
        "plots": [],
    }
    main_plots = {
        "histostats": [],
        "medianstats": [],
        "deciledmedian": []
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
    report["sampleinfo"].update({svar: sample_info[svar] for svar in SAMPLEINFO_VARS})

    # Loading all dge data summaries
    # Table: summarised metrics over beads
    dge_summaries = {}
    for run_mode in run_modes:
        dge_summaries[run_mode] = {}
        dge_summaries[run_mode] = load_dge_summary_downsampling(downsampled_dge_summary, run_mode, puck_barcode_file_id)

    # Plots
    # Histograms per run mode
    for run_mode, dge_summary in dge_summaries.items():
        umicutoff = {"name": run_mode, "umiplot": None, "readsplot": None, "readsumiplot": None}
        fig, _ = plot_density_metric_faceted(dge_summary, "total_counts", log_scale=True, color=clrs['umis'], title='# of UMIs per spatial unit')
        umicutoff["umiplot"] = plot_to_base64(fig)

        fig, _ = plot_density_metric_faceted(dge_summary, "n_reads", log_scale=True, color=clrs['reads'], title='# of reads per spatial unit')
        umicutoff["readsplot"] = plot_to_base64(fig)

        fig, _ = plot_density_metric_faceted(dge_summary, "reads_per_counts", log_scale=False, color=clrs['pcr'], title='reads/UMI per spatial unit')
        umicutoff["readsumiplot"] = plot_to_base64(fig)

        report["plots"]["histostats"].append(umicutoff)

    # Median plots per run mode
    for run_mode, dge_summary in dge_summaries.items():
        umi_cutoffs_values = project_df.config.get_run_mode(run_mode).variables['umi_cutoff']
        umi_cutoffs_values = list(sorted(list(set([int(u) for u in umi_cutoffs_values] + [1]))))
        medianstats = {"name": run_mode, "umiplot": None, "readsplot": None, "readsumiplot": None}
        fig, _ = plot_median_per_run_mode(dge_summary, "total_counts", umi_cutoffs_values, color=clrs['umis'], title="median reads\nper spatial unit")
        medianstats["umiplot"] = plot_to_base64(fig)

        fig, _ = plot_median_per_run_mode(dge_summary, "n_reads", umi_cutoffs_values, color=clrs['reads'], title="median UMIs\nper spatial unit")
        medianstats["readsplot"] = plot_to_base64(fig)

        fig, _ = plot_median_per_run_mode(dge_summary, "reads_per_counts", umi_cutoffs_values, color=clrs['pcr'], title="median reads/UMI\nper spatial unit")
        medianstats["readsumiplot"] = plot_to_base64(fig)

        report["plots"]["medianstats"].append(medianstats)


    # Deciled plots
    for run_mode, dge_summary in dge_summaries.items():
        deciledmedian = {"name": run_mode, "plot": None}
        decile_dat = generate_deciled_data(dge_summary)
        fig, _ = plot_deciled_median(decile_dat)
        deciledmedian["plot"] = plot_to_base64(fig)
        report["plots"]["deciledmedian"].append(deciledmedian)

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
        description="generate spacemake's 'saturation analysis' with python",
    )
    parser = setup_parser(parser)

    args = parser.parse_args()
    template_file = os.path.join(absolute_path, "templates/saturation_analysis.html")

    report_metadata = generate_saturation_analysis_metadata(
        args.project_id,
        args.sample_id,
        args.run_modes,
        args.downsampled_dge_summary,
        args.puck_barcode_file_id
    )

    html_report = generate_html_report(report_metadata, template_file)

    with open(args.out_html_report, "w") as output:
        output.write(html_report)


if __name__ == "__main__":
    cmdline()