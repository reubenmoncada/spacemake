import logging

import multiprocessing as mp
import pandas as pd
import time
import networkx as nx
import numpy as np

from spacemake.util import message_aggregation, FASTQ_src

"""
Sequence intersection between a query file and target files.
Specifically designed for matching spatial pucks against a collection
of sample's reads (e.g., Open-ST).

Usage:
    python n_intersect_sequences.py \
        --query input_reads.txt \
        --target target_file1.txt target_file2.txt \
        --target-id id1 id2 \
        --query-plain-skip 0 \
        --query-plain-column 1 \
        --target-plain-column 0 \
        --summary-output output_summary.txt

Notes:
    Optionally, a --target-adjacency-file can be provided to specify
    the spatial adjacency of pucks/tiles, to perform an additional
    filtering to increase TP+TN and reduce FP+FN. This file is in the
    'edgelist' format (from networkx), where node IDs = tile/puck IDs.

Author:
    Daniel León-Periñán
"""

logger_name = "spacemake.snakemake.scripts.n_intersect_sequences"
logger = logging.getLogger(logger_name)
AVAILABLE_CPUS = mp.cpu_count()

def setup_parser(parser):
    """
    Set up command-line arguments for the script.

    :param parser: Argument parser object.
    :type parser: argparse.ArgumentParser
    :returns: Updated argument parser object.
    :rtype: argparse.ArgumentParser
    """
    parser.add_argument(
        "--query",
        type=str,
        help="query reads file for lookup into 'target'(s) database",
        default=None,
    )

    parser.add_argument(
        "--query-tag",
        type=str,
        help="if input is a bam file, the specified tag will be used as the sequence",
        default="CB",
    )

    parser.add_argument(
        "--query-plain-skip",
        type=int,
        help="number of lines to skip, if target input is a plain text file",
        default=None,
    )

    parser.add_argument(
        "--query-plain-column",
        type=int,
        help="column to parse from query, if input is a plain text file",
        default=None,
    )

    parser.add_argument(
        "--query-separator",
        type=str,
        default="\t",
        help="if input is a tabular plain text file, this specifies the character for column separation",
        choices=["\t", ",", ";"],
    )

    parser.add_argument(
        "--target",
        type=str,
        nargs="+",
        help="target puck files against which search is performed",
        required=True,
    )

    parser.add_argument(
        "--target-id",
        type=str,
        nargs="+",
        help="target puck ids for summary output",
        required=True,
    )

    parser.add_argument(
        "--target-tag",
        type=str,
        help="if input is a bam file, the specified tag will be used as the sequence",
        default="CB",
    )

    parser.add_argument(
        "--target-plain-skip",
        type=int,
        help="number of lines to skip, if target input is a plain text file",
        default=None,
    )

    parser.add_argument(
        "--target-plain-column",
        type=int,
        help="column to parse from target, if input is a plain text file",
        default=None,
    )

    parser.add_argument(
        "--target-separator",
        type=str,
        default="\t",
        help="if input is a tabular plain text file, this specifies the character for column separation",
        choices=["\t", ",", ";"],
    )

    parser.add_argument(
        "--target-adjacency-file",
        type=str,
        help="networkx edgelist for the physical adjacency of pucks (e.g., for multi-puck data as Open-ST)",
        default=""
    )

    parser.add_argument(
        "--summary-output",
        type=str,
        help="a summary output file containing the number of matches between query and target, per target file",
        required=True,
    )

    parser.add_argument(
        "--min-threshold",
        type=float,
        default=0,
        help="targets with 'matching_ratio' above --min-threshold are marked as True; else, False.",
        required=False,
    )

    parser.add_argument(
        "--n-jobs",
        type=int,
        help="number of parallel jobs (up to {AVAILABLE_CPUS} in this machine) for database lookup",
        metavar=f"[0-{AVAILABLE_CPUS-1}]",
        choices=range(0, AVAILABLE_CPUS),
        default=1,
    )

    return parser


def tile_community_detect(target_adjacency, target_list):
    """
    Generate as many subgraphs as communities found in the graph. 
    The edges of the target_adjacency graph are subset to 
    those edges where both nodes are in the target_list.

    :param target_adjacency: The adjacency graph.
    :type target_adjacency: networkx.Graph
    :param target_list: List of nodes to consider for community detection.
    :type target_list: list
    :return: List of graphs representing communities.
    :rtype: list
    """
    subgraph = target_adjacency.subgraph(target_list)

    for u, v in subgraph.edges:
        matching_ratio_u = subgraph.nodes[u].get('matching_ratio', 0)
        matching_ratio_v = subgraph.nodes[v].get('matching_ratio', 0)
        weight = (matching_ratio_u * matching_ratio_v) ** 0.5
        subgraph[u][v]['weight'] = weight

    communities = nx.algorithms.community.label_propagation.label_propagation_communities(subgraph)
    community_graphs = [subgraph.subgraph(community) for community in communities]

    return community_graphs

def filter_communities_by_sum_matching(target_communities):
    """
    The target_communities is a list of graphs. 
    
    At each node, there is a 'matching_ratio' value. 
    This function will return the subgraph with the maximum total
    'n_matching' across all nodes.
    
    :param target_communities: List of graphs representing communities.
    :type target_communities: list
    :return: Graph with the maximum sum 'n_matching'.
    :rtype: networkx.Graph
    """
    sum_matching_ratios = [np.array(list(nx.get_node_attributes(community, 'n_matching').values())).sum() for community in target_communities]
    return target_communities[np.argmax(sum_matching_ratios)]

def dilate_tiles(target_adjacency, target_list):
    """
    This function labels the edges of the target_adjacency as 1 if
    any of the nodes of the edge is in the passed_tiles list, and then returns
    a list of all nodes that have common edges with value 1 
    (equivalent of applying a dilation operation in computer vision)
    
    :param target_adjacency: The adjacency graph.
    :type target_adjacency: networkx.Graph
    :param target_list: List of nodes to consider for edge labeling.
    :type target_list: list
    :return: List of nodes with common edges labeled as 1.
    :rtype: list
    """
    dilated_nodes = []
    
    for edge in target_adjacency.edges:
        node1, node2 = edge
        
        if node1 in target_list or node2 in target_list:
            target_adjacency.edges[node1, node2]['dilation'] = 1
            dilated_nodes.append(node1)
            dilated_nodes.append(node2)

    dilated_nodes = list(set(dilated_nodes))
    
    return dilated_nodes


def BAM_src(src: str, tag: str = None) -> tuple:
    """
    Generate read data from a BAM file.

    :param src: Path to the BAM file.
    :type src: str
    :param tag: Tag to be used as the sequence. Defaults to None.
    :type tag: str, optional
    :yields: Tuple containing read name, sequence, and quality.
    :rtype: tuple
    """

    import pysam

    bam = pysam.AlignmentFile(src, "rb", check_sq=False)
    for read in bam.fetch(until_eof=True):
        if tag is None:
            yield read.query_name, read.query_sequence, read.query_qualities
        else:
            yield None, read.get_tag(tag), None


def plain_src(f: str, skip: int = 0, column: int = 0, separator: str = "\t") -> tuple:
    """
    Generate read data from a plain text file.

    :param f: Path to the plain text file.
    :type f: str
    :param skip: Number of lines to skip. Defaults to 0.
    :type skip: int, optional
    :param column: Column to parse from the file. Defaults to 0.
    :type column: int, optional
    :param separator: Character for column separation. Defaults to "\t".
    :type separator: str, optional
    :yields: Tuple containing read name, sequence, and quality.
    :rtype: tuple
    """
    
    reads = pd.read_csv(f, sep=separator, skiprows=skip)
    reads = reads.iloc[:, column]

    for read in reads:
        yield None, read, None


def read_with_tag(
    f: str, tag: str = None, skip: int = None, column: int = 0, separator: str = "\t"
) -> str:  
    """
    Read data from various sources based on the file type.

    :param f: Path to the input file.
    :type f: str
    :param tag: Tag to be used as the sequence. Defaults to None.
    :type tag: str, optional
    :param skip: Number of lines to skip (plain text files only). Defaults to None.
    :type skip: int, optional
    :param column: Column to parse from the file (plain text files only). Defaults to 0.
    :type column: int, optional
    :param separator: Character for column separation (plain text files only). Defaults to "\t".
    :type separator: str, optional
    :yields: Sequence from the input file.
    :rtype: str
    """
    import gzip

    if type(f) is not str:
        src = FASTQ_src(f)  # assume its a stream or file-like object already
    elif f.endswith(".txt") or f.endswith(".txt.gz"):
        src = plain_src(f, skip, column, separator)
    elif f.endswith(".gz"):
        src = FASTQ_src(gzip.open(f, mode="rt"))
    elif f.endswith(".bam"):
        src = BAM_src(f, tag=tag)
    else:
        src = FASTQ_src(open(f))

    for _, seq, _ in src:
        yield seq


# TODO: use a partial function to pass args to not use global context
def find_matches(f: str):
    """
    Find matches between query and target sequences.

    :param f: Target file to compare against query sequences.
    :type f: str
    :returns: Tuple containing information about the target file, number of barcodes, number of matching barcodes, and the matching ratio.
    :rtype: tuple
    """
    global query_seqs, args

    start = time.time()
    target = set(
        read_with_tag(
            f,
            args.target_tag,
            args.target_plain_skip,
            args.target_plain_column,
            args.target_separator,
        )
    )
    n_matches = len(target.intersection(query_seqs))
    pct_matches_target = n_matches / len(target)
    print(f"queried {f} in {round(time.time()-start, 2)} s")

    return (f, len(target), n_matches, pct_matches_target)


@message_aggregation(logger_name)
def cmdline():
    """cmdline."""
    import argparse

    global args, query_seqs

    parser = argparse.ArgumentParser(
        allow_abbrev=False,
        description="calculate sequence intersection between a query and target(s) file(s)",
    )
    parser = setup_parser(parser)

    args = parser.parse_args()

    if len(args.target_id) != len(args.target):
        raise ValueError(
            f"target_id ({len(args.target_id)}) and target ({len(args.target)}) are different in size"
        )

    query_seqs = read_with_tag(
        args.query,
        args.query_tag,
        args.query_plain_skip,
        args.query_plain_column,
        args.query_separator,
    )
    logger.info(f"read query file from '{args.query}'")

    # get unique reads into a global context
    query_seqs = set(query_seqs)
    logger.info(f"hashed unique query reads")
    logger.info(
        f"querying against {len(args.target)} targets with {args.n_jobs} parallel jobs"
    )

    with mp.Pool(args.n_jobs) as pool:
        results = pool.map(find_matches, args.target)

    df = pd.DataFrame(
        results,
        columns=["puck_barcode_file", "n_barcodes", "n_matching", "matching_ratio"],
    )
    df["puck_barcode_file_id"] = args.target_id
    df['pass_threshold'] = 0
    df['pass_threshold'][df['matching_ratio'] > args.min_threshold] = 1
    df.to_csv(args.summary_output, index=False)

    if args.target_adjacency_file:
        logging.info("filtering tiles by adjacency")
        target_adjacency = nx.read_edgelist(args.target_adjacency_file)
        passed_tiles = df[df['pass_threshold'] == 1]['puck_barcode_file_id'].tolist()

        if not all(tile in target_adjacency.nodes for tile in passed_tiles):
            raise ValueError("not all passed tiles are available in the graph.")

        for node in target_adjacency.nodes:
            if node in df['puck_barcode_file_id'].values:
                matching_ratio = df.loc[df['puck_barcode_file_id'] == node, 'matching_ratio'].iloc[0]
            else:
                matching_ratio = 0
            target_adjacency.nodes[node]['matching_ratio'] = matching_ratio

        target_communities = tile_community_detect(target_adjacency, passed_tiles)
        target_best_community = filter_communities_by_sum_matching(target_communities)
        best_community_nodes = list(target_best_community.nodes)
        dilated_best_community_nodes = dilate_tiles(target_adjacency, best_community_nodes)
        
        print(dilated_best_community_nodes)
        df['pass_adjacency'] = 0
        df['pass_adjacency'][df['puck_barcode_file_id'].isin(dilated_best_community_nodes)] = 1
        
        logging.info("rewriting matched barcodes after adjacency filter")
        df.to_csv(args.summary_output, index=False)


    logging.info(f"tile statistics:\n"+
                 f"TOTAL: {len(df)}\n"+
                 f"PASS: {df['pass_threshold'].sum()}\n"+
                 f"AVERAGE MATCHING RATIO: {df['matching_ratio'][df['pass_threshold'] == 1].mean()}"+
                 f"PASS ADJACENCY: {df['pass_adjacency'].sum()}")

if __name__ == "__main__":
    cmdline()