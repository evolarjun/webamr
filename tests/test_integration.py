"""
Integration tests for AMRFinderPlus running inside the Docker container.
These tests require the `amr-worker-local` Docker image to be built.

Run with:
    pytest tests/test_integration.py -v
"""
import subprocess
import os
import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
IMAGE = "amr-worker-local"

def docker_run(cmd_args: list[str]) -> subprocess.CompletedProcess:
    """Run amrfinder inside the Docker container with the tests/ dir mounted."""
    return subprocess.run(
        [
            "docker", "run", "--rm",
            "-v", f"{TESTS_DIR}:/data",
            IMAGE,
        ] + cmd_args,
        capture_output=True,
        text=True,
    )

def check_tsv_header(output: str):
    """Assert the output looks like an AMRFinderPlus TSV (has the expected header columns)."""
    lines = [l for l in output.strip().splitlines() if l]
    assert len(lines) >= 1, "Output is empty"
    header = lines[0].split("\t")
    # Column names as of AMRFinderPlus v4.x
    expected_cols = {"Element symbol", "Method", "% Identity to reference", "Type"}
    assert expected_cols.issubset(set(header)), (
        f"Missing expected columns.\nGot: {header}"
    )
    return lines


# ---------------------------------------------------------------------------
# 1. Sanity checks
# ---------------------------------------------------------------------------

class TestSanity:
    def test_amrfinder_binary_present(self):
        result = docker_run(["amrfinder", "--version"])
        assert result.returncode == 0, result.stderr
        # v4.2.7 outputs just the version number, not the name
        version = result.stdout.strip()
        parts = version.split(".")
        assert len(parts) == 3 and parts[0].isdigit(), (
            f"Unexpected --version output: {version!r}"
        )

    def test_hmmer_present(self):
        result = docker_run(["hmmsearch", "-h"])
        assert result.returncode in (0, 1)  # hmmsearch -h exits 1 intentionally
        assert "hmmsearch" in result.stdout.lower() or "hmmsearch" in result.stderr.lower()

    def test_blast_present(self):
        result = docker_run(["blastp", "-version"])
        assert result.returncode == 0, result.stderr
        assert "blastp" in result.stdout.lower()


# ---------------------------------------------------------------------------
# 2. Protein FASTA input
# ---------------------------------------------------------------------------

class TestProteinInput:
    def test_basic_protein_run(self):
        result = docker_run(["amrfinder", "-p", "/data/test_prot.fa"])
        assert result.returncode == 0, f"STDERR: {result.stderr}"

    def test_protein_output_has_tsv_header(self):
        result = docker_run(["amrfinder", "-p", "/data/test_prot.fa"])
        assert result.returncode == 0, result.stderr
        check_tsv_header(result.stdout)

    def test_protein_finds_known_gene(self):
        """test_prot.fa contains blaTEM-156; AMRFinder should find it."""
        result = docker_run(["amrfinder", "-p", "/data/test_prot.fa"])
        assert result.returncode == 0, result.stderr
        assert "blaTEM" in result.stdout, (
            f"Expected blaTEM in output:\n{result.stdout}"
        )

    def test_protein_plus_flag(self):
        """--plus should add stress/virulence genes without crashing."""
        result = docker_run(["amrfinder", "-p", "/data/test_prot.fa", "--plus"])
        assert result.returncode == 0, f"STDERR: {result.stderr}"
        check_tsv_header(result.stdout)

    def test_protein_with_ident_min(self):
        result = docker_run([
            "amrfinder", "-p", "/data/test_prot.fa",
            "--ident_min", "0.9",
        ])
        assert result.returncode == 0, f"STDERR: {result.stderr}"

    def test_protein_with_coverage_min(self):
        result = docker_run([
            "amrfinder", "-p", "/data/test_prot.fa",
            "--coverage_min", "0.8",
        ])
        assert result.returncode == 0, f"STDERR: {result.stderr}"

    def test_protein_with_organism(self):
        result = docker_run([
            "amrfinder", "-p", "/data/test_prot.fa",
            "--organism", "Escherichia",
        ])
        assert result.returncode == 0, f"STDERR: {result.stderr}"
        check_tsv_header(result.stdout)


# ---------------------------------------------------------------------------
# 3. DNA FASTA input
# ---------------------------------------------------------------------------

class TestDnaInput:
    def test_basic_dna_run(self):
        result = docker_run(["amrfinder", "-n", "/data/test_dna.fa"])
        assert result.returncode == 0, f"STDERR: {result.stderr}"

    def test_dna_output_has_tsv_header(self):
        result = docker_run(["amrfinder", "-n", "/data/test_dna.fa"])
        assert result.returncode == 0, result.stderr
        check_tsv_header(result.stdout)

    def test_dna_finds_known_gene(self):
        """test_dna.fa has contig01 blaTEM-156_cds; AMRFinder should find blaTEM."""
        result = docker_run(["amrfinder", "-n", "/data/test_dna.fa"])
        assert result.returncode == 0, result.stderr
        assert "blaTEM" in result.stdout, (
            f"Expected blaTEM in output:\n{result.stdout}"
        )

    def test_dna_plus_flag(self):
        result = docker_run(["amrfinder", "-n", "/data/test_dna.fa", "--plus"])
        assert result.returncode == 0, f"STDERR: {result.stderr}"


# ---------------------------------------------------------------------------
# 4. Combined protein + GFF input
# ---------------------------------------------------------------------------

class TestProteinWithGff:
    def test_protein_with_gff(self):
        """AMRFinder can annotate genomic coordinates when given -g (GFF)."""
        result = docker_run([
            "amrfinder",
            "-p", "/data/test_prot.fa",
            "-g", "/data/test_prot.gff",
        ])
        assert result.returncode == 0, f"STDERR: {result.stderr}"
        check_tsv_header(result.stdout)

    def test_protein_gff_output_has_contig_column(self):
        result = docker_run([
            "amrfinder",
            "-p", "/data/test_prot.fa",
            "-g", "/data/test_prot.gff",
        ])
        assert result.returncode == 0, result.stderr
        lines = result.stdout.strip().splitlines()
        header = lines[0].split("\t")
        # v4.x uses 'Contig id' when GFF provided
        assert "Contig id" in header, f"Expected 'Contig id' column, got: {header}"
