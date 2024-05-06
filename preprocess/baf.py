# baf.py - preprocess the input BAM file to generate reference-phased cell x gene AD & DP matrices.

import getopt
import os
import sys

from logging import error, info
from xcltk.baf.genotype import pileup, ref_phasing, vcf_add_genotype
from xcltk.utils.base import assert_e, assert_n
from xcltk.utils.vcf import vcf_index, vcf_merge, vcf_split_chrom
from xcltk.utils.xlog import init_logging


def usage(fp = sys.stdout):
    s =  "\n" 
    s += "Version: %s\n" % VERSION
    s += "Usage:   %s [options]\n" % APP
    s += "\n" 
    s += "Options:\n"
    s += "  --label STR        Task label.\n"
    s += "  --sam FILE         Comma separated indexed BAM/CRAM file(s).\n"
    s += "  --samlist FILE     A file listing BAM/CRAM files, each per line.\n"
    s += "  --barcode FILE     A plain file listing all effective cell barcodes (for 10x)\n"
    s += "                     or sample IDs (for smartseq).\n"
    s += "  --snpvcf FILE      A vcf file listing all candidate SNPs.\n"
    s += "  --outdir DIR       Output dir.\n"
    s += "  --gmap FILE        Path to genetic map provided by Eagle2\n"
    s += "                     (e.g. Eagle_v2.4.1/tables/genetic_map_hg38_withX.txt.gz).\n"
    s += "  --eagle FILE       Path to Eagle2 binary file.\n"
    s += "  --paneldir DIR     Directory to phasing reference panel (BCF files).\n"
    s += "  --version          Print version and exit.\n"
    s += "  --help             Print this message and exit.\n"
    s += "\n"
    s += "Optional arguments:\n"
    s += "  --cellTAG STR      Cell barcode tag [%s]\n" % CELL_TAG
    s += "  --UMItag STR       UMI tag [%s]\n" % UMI_TAG
    s += "  --ncores INT       Number of threads [%d]\n" % N_CORES
    s += "  --smartseq         Run in smartseq mode.\n"
    s += "  --bulk             Run in bulk mode.\n"
    s += "\n"
    s += "Notes:\n"
    s += "  1. One and only one of `--sam` and `--samlist` should be specified.\n"
    s += "  2. For smartseq data, the order of the BAM files (in `--sam` or `--samlist`)\n"
    s += "     and the sample IDs (in `--barcode`) should match each other.\n"
    s += "\n"

    fp.write(s)


def main(argv):
    if len(argv) < 2:
        usage()
        sys.exit(1)

    init_logging(stream = sys.stdout)

    label = None
    sam_fn = sam_list_fn = barcode_fn = snp_vcf_fn = None
    out_dir = None
    gmap_fn = eagle_fn = panel_dir = None
    cell_tag, umi_tag = CELL_TAG, UMI_TAG
    ncores = N_CORES
    mode = "10x"

    opts, args = getopt.getopt(
        args = argv[1:],
        shortopts = "", 
        longopts = [
            "label=",
            "sam=", "samlist=", "barcode=", "snpvcf=",
            "outdir=",
            "gmap=", "eagle=", "paneldir=",
            "version", "help",
            
            "cellTAG=", "UMItag=",
            "ncores=",
            "smartseq", "bulk"
        ])

    for op, val in opts:
        if len(op) > 2:
            op = op.lower()
        if op in ("--label"): label = val
        elif op in ("--sam"): sam_fn = val
        elif op in ("--samlist"): sam_list_fn = val
        elif op in ("--barcode"): barcode_fn = val
        elif op in ("--snpvcf"): snp_vcf_fn = val
        elif op in ("--outdir"): out_dir = val
        elif op in ("--gmap"): gmap_fn = val
        elif op in ("--eagle"): eagle_fn = val
        elif op in ("--paneldir"): panel_dir = val
        elif op in ("--version"): error(VERSION); sys.exit(1)
        elif op in ("--help"): usage(); sys.exit(1)

        elif op in ("--celltag"): cell_tag = val
        elif op in ("--umitag"): umi_tag = val
        elif op in ("--ncores"): ncores = int(val)     # keep it in `str` format.
        elif op in ("--smartseq"): mode = "smartseq"
        elif op in ("--bulk"): mode = "bulk"
        else:
            error("invalid option: '%s'." % op)
            return(-1)
        
    ret = run_baf_preprocess(
        label = label,
        sam_fn = sam_fn, sam_list_fn = sam_list_fn, barcode_fn = barcode_fn,
        snp_vcf_fn = snp_vcf_fn,
        out_dir = out_dir,
        gmap_fn = gmap_fn, eagle_fn = eagle_fn, panel_dir = panel_dir,
        cell_tag = cell_tag, umi_tag = umi_tag,
        ncores = ncores,
        mode = mode
    )
    
    info("All Done!")

    return(ret)
        

def run_baf_preprocess(
    label,
    sam_fn = None, sam_list_fn = None, barcode_fn = None,
    snp_vcf_fn = None,
    out_dir = None,
    gmap_fn = None, eagle_fn = None, panel_dir = None,
    cell_tag = "CB", umi_tag = "UB",
    ncores = 1,
    mode = "10x"
):
    info("xcltk BAF preprocessing starts ...")

    # check args
    info("check args ...")

    assert_n(label)
    sample = label if mode == "bulk" else None
    assert_e(snp_vcf_fn)
    if not os.path.exists(out_dir):
        os.mkdir(out_dir)

    assert_e(gmap_fn)
    assert_e(eagle_fn)
    assert_e(panel_dir)
    for chrom in range(1, 23):
        assert_e(os.path.join(panel_dir, "chr%d.genotypes.bcf" % chrom))
        assert_e(os.path.join(panel_dir, "chr%d.genotypes.bcf.csi" % chrom))

    genome = "hg19" if "hg19" in gmap_fn else "hg38"

    # other args will be checked in pileup() and ref_phasing().
    info("run in '%s' mode (genome version '%s')" % (mode, genome))

    # pileup
    info("start pileup ...")

    pileup_dir = os.path.join(out_dir, "pileup")
    if not os.path.exists(pileup_dir):
        os.mkdir(pileup_dir)
    pileup_script = os.path.join(out_dir, "run_pileup.sh")
    pileup_log_fn = os.path.join(out_dir, "pileup.log")

    pileup(
        sam_fn = sam_fn, sam_list_fn = sam_list_fn,
        barcode_fn = barcode_fn, sample = sample,
        snp_vcf_fn = snp_vcf_fn,
        out_dir = pileup_dir,
        mode = mode,
        cell_tag = cell_tag, umi_tag = umi_tag,
        ncores = ncores,
        min_maf = 0.1, min_count = 20,
        script_fn = pileup_script,
        log_fn = pileup_log_fn
    )

    pileup_vcf_fn = os.path.join(pileup_dir, "cellSNP.base.vcf.gz")
    assert_e(pileup_vcf_fn)

    info("pileup VCF is '%s'." % pileup_vcf_fn)

    # prepare VCF files for phasing
    info("prepare VCF files for phasing ...")

    phasing_dir = os.path.join(out_dir, "phasing")
    if not os.path.exists(phasing_dir):
        os.mkdir(phasing_dir)
    phasing_script = os.path.join(out_dir, "run_phasing.sh")
    phasing_log_fn = os.path.join(out_dir, "phasing.log")

    # add genotypes
    info("add genotypes ...")

    genotype_vcf_fn = os.path.join(out_dir, "%s.genotype.vcf.gz" % label)
    vcf_add_genotype(
        in_fn = pileup_vcf_fn,
        out_fn = genotype_vcf_fn,
        sample = label,
        chr_prefix = True,     # add "chr" prefix
        sort = True,
        unique = True
    )

    # split VCF by chromosomes.
    info("split VCF by chromosomes ...")

    valid_chroms = []       # has "chr" prefix
    target_vcf_list = []
    res = vcf_split_chrom(
        fn = genotype_vcf_fn,
        out_dir = phasing_dir,
        label = label,
        chrom_list = ["chr" + str(i) for i in range(1, 23)],
        out_prefix_list = None,
        verbose = True
    )
    for chrom, n_variants, vcf_fn in res:
        if n_variants > 0:
            valid_chroms.append(chrom)
            target_vcf_list.append(vcf_fn)
            vcf_index(vcf_fn)

    info("%d chromosome VCFs are outputted with variants." % len(valid_chroms))
    #os.remove(genotype_vcf_fn)

    # reference phasing
    info("reference phasing ...")

    ref_vcf_list = [os.path.join(panel_dir, "%s.genotypes.bcf" % chrom) \
                    for chrom in valid_chroms]
    out_prefix_list = [os.path.join(phasing_dir, "%s_%s.phased" % \
                    (label, chrom)) for chrom in valid_chroms]
    ref_phasing(
        target_vcf_list = target_vcf_list,
        ref_vcf_list = ref_vcf_list,
        out_prefix_list = out_prefix_list,
        gmap_fn = gmap_fn,
        eagle_fn = eagle_fn,
        out_dir = phasing_dir,
        ncores = ncores,
        script_fn = phasing_script,
        log_fn = phasing_log_fn,
        verbose = True
    )

    info("phased VCFs are in dir '%s'." % phasing_dir)

    # merge phased VCFs
    info("merge phased VCFs ...")

    phased_vcf_list = ["%s.vcf.gz" % prefix for prefix in out_prefix_list]
    for fn in phased_vcf_list:
        assert_e(fn)

    phased_vcf_fn = os.path.join(out_dir, "%s.phased.vcf.gz" % label)
    vcf_merge(phased_vcf_list, phased_vcf_fn, sort = True)

    info("merged VCF is '%s'." % phased_vcf_fn)

    # count allele counts for each feature.
    


APP = "baf.py"
VERSION = "0.0.1"

CELL_TAG = "CB"
N_CORES = 1
UMI_TAG = "UB"


if __name__ == "__main__":
    main(sys.argv)