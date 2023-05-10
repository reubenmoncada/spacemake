import importlib.metadata
__version__ = "0.7.2"
__author__ = ["Nikos Karaiskos", "Tamas Ryszard Sztanka-Toth", "Marvin Jens"]
__license__ = "GPL"
__email__ = [
    "nikolaos.karaiskos@mdc-berlin.de",
    "tamasryszard.sztanka-toth@mdc-berlin.de",
    "marvin.jens@charite.de",
]

author_contributions = """
SPACEMAKE is built on snakemake scripts originally developed by Nikos Karaiskos
for the analysis of dropseq data. These gradually evolved into a robust workflow for
spatial transcriptomics data analysis that was improved and generalized to work
with different ST technologies by Tamas Ryszard Sztanka-Toth. Marvin Jens contributed
longread analysis code and support for converting fastq to BAM as a first step.
Many features of the automated analysis and integration with Novosparc were added by
Tamas, in close collaboration with Nikos, culminating in the first spacemake
publication:

    https://doi.org/10.1093/gigascience/giac064

Marvin then added new building blocks to successively replace the java-based 
dropseq tools with python/pysam based code: cutadapt_bam.py, annotator.py, as well
as the ability to align raw reads to multiple indices, in close collaboration
with Nikos & Tamas.
These changes, together with support for the much larger numbers of spatial barcodes 
generated by Stereo-seq, seq-scope, et al. will probably lead to a SPACEMAKE2 
release/paper in the near future.
"""

roadmap = [
    ("0.5.5", "universal ST support and utility, novosparc integration. Sztanka-Toth et al. 2022"),
    ("0.7", "support multiple mapping indices, bulk samples, custom user-defined snakemake rules"),
    ("1.x", "replace dropseq tools. Own annotator and towards entirely scanpy workflow"),
    ("1.x", "efficient handling of 1E8+ spatial barcodes (seq-scope etc.)"),
    ("1.x", "add interactive data exploration support (shiny?)"),
    ("2.x", "cmdline interface cleanup and remote API support"),
    ("2.x", "cython magic to speed up parallel BAM processing via shared memory"),
]
