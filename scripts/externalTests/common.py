#!/usr/bin/env python3

# ------------------------------------------------------------------------------
# This file is part of solidity.
#
# solidity is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# solidity is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with solidity.  If not, see <http://www.gnu.org/licenses/>
#
# (c) 2023 solidity contributors.
# ------------------------------------------------------------------------------

import os
import re
import subprocess
import sys
from abc import ABCMeta
from argparse import ArgumentParser
from dataclasses import dataclass, field
from pathlib import Path
from shutil import rmtree
from tempfile import mkdtemp
from textwrap import dedent
from typing import List

# Our scripts/ is not a proper Python package so we need to modify PYTHONPATH to import from it
# pragma pylint: disable=import-error,wrong-import-position
PROJECT_ROOT = Path(__file__).parents[2]
sys.path.insert(0, f"{PROJECT_ROOT}/scripts/common")

from git_helpers import git_commit_hash

SOLC_FULL_VERSION_REGEX = re.compile(r"^[a-zA-Z: ]*(.*)$")
SOLC_SHORT_VERSION_REGEX = re.compile(r"^([0-9.]+).*\+|\-$")

evm_version = os.environ.get("DEFAULT_EVM")
CURRENT_EVM_VERSION: str = evm_version if evm_version is not None else "shanghai"

AVAILABLE_PRESETS: List[str] = [
    "legacy-no-optimize",
    "ir-no-optimize",
    "legacy-optimize-evm-only",
    "ir-optimize-evm-only",
    "legacy-optimize-evm+yul",
    "ir-optimize-evm+yul",
]


@dataclass
class TestConfig:
    name: str
    repo_url: str
    ref_type: str
    ref: str
    build_dependency: str = field(default="nodejs")
    compile_only_presets: List[str] = field(default_factory=list)
    settings_presets: List[str] = field(default_factory=lambda: AVAILABLE_PRESETS)
    evm_version: str = field(default=CURRENT_EVM_VERSION)

    def selected_presets(self):
        return set(self.compile_only_presets + self.settings_presets)


class TestRunner(metaclass=ABCMeta):
    config: TestConfig
    solc_binary_type: str
    solc_binary_path: Path

    def __init__(self, argv, config: TestConfig):
        args = parse_command_line(f"{config.name} external tests", argv)
        self.config = config
        self.solc_binary_type = args.solc_binary_type
        self.solc_binary_path = args.solc_binary_path
        self.env = os.environ.copy()
        self.tmp_dir = mkdtemp(prefix=f"ext-test-{config.name}-")
        self.test_dir = Path(self.tmp_dir) / "ext"

    def setup_solc(self) -> str:
        if self.solc_binary_type == "solcjs":
            # TODO: add support to solc-js
            raise NotImplementedError()
        print("Setting up solc...")
        solc_version_output = subprocess.getoutput(f"{self.solc_binary_path} --version").split(":")[1]
        return parse_solc_version(solc_version_output)

    @staticmethod
    def on_local_test_dir(fn):
        """Run a function inside the test directory"""

        def f(self, *args, **kwargs):
            assert self.test_dir is not None
            os.chdir(self.test_dir)
            return fn(self, *args, **kwargs)

        return f

    def setup_environment(self):
        """Configure the project build environment"""
        print("Configuring Runner building environment...")
        replace_version_pragmas(self.test_dir)

    @on_local_test_dir
    def clean(self):
        """Clean temporary directories"""
        rmtree(self.tmp_dir)

    @on_local_test_dir
    def compiler_settings(self, _: List[str]):
        # TODO: default to hardhat
        raise NotImplementedError()

    @on_local_test_dir
    def compile(self, _: str):
        # TODO: default to hardhat
        raise NotImplementedError()

    @on_local_test_dir
    def run_test(self):
        # TODO: default to hardhat
        raise NotImplementedError()


# Helper functions
def compiler_settings(evm_version: str, via_ir: str = "false", optimizer: str = "false", yul: str = "false") -> dict:
    return {
        "optimizer": {"enabled": optimizer, "details": {"yul": yul}},
        "evmVersion": evm_version,
        "viaIR": via_ir,
    }


def settings_from_preset(preset: str, evm_version: str) -> dict:
    assert preset in AVAILABLE_PRESETS
    switch = {
        "legacy-no-optimize": compiler_settings(evm_version),
        "ir-no-optimize": compiler_settings(evm_version, via_ir="true"),
        "legacy-optimize-evm-only": compiler_settings(evm_version, optimizer="true"),
        "ir-optimize-evm-only": compiler_settings(evm_version, via_ir="true", optimizer="true"),
        "legacy-optimize-evm+yul": compiler_settings(evm_version, optimizer="true", yul="true"),
        "ir-optimize-evm+yul": compiler_settings(evm_version, via_ir="true", optimizer="true", yul="true"),
    }
    assert preset in switch
    return switch[preset]


def parse_command_line(description: str, args: List[str]):
    arg_parser = ArgumentParser(description)
    arg_parser.add_argument(
        "solc_binary_type",
        metavar="solc-binary-type",
        type=str,
        default="native",
        choices=["native", "solcjs"],
        help="""Solidity compiler binary type""",
    )
    arg_parser.add_argument(
        "solc_binary_path",
        metavar="solc-binary-path",
        type=Path,
        default=Path("/usr/local/bin/solc"),
        help="""Path to solc or soljson.js binary""",
    )
    return arg_parser.parse_args(args)


def download_project(test_dir: Path, repo_url: str, ref_type: str = "branch", ref: str = "master"):
    assert ref_type in ("commit", "branch", "tag")

    print(f"Cloning {ref_type} {ref} of {repo_url}...")
    if ref_type == "commit":
        os.mkdir(test_dir)
        os.chdir(test_dir)
        subprocess.run(["git", "init"], check=True)
        subprocess.run(["git", "remote", "add", "origin", repo_url], check=True)
        subprocess.run(["git", "fetch", "--depth", "1", "origin", ref], check=True)
        subprocess.run(["git", "reset", "--hard", "FETCH_HEAD"], check=True)
    else:
        os.chdir(test_dir.parent)
        subprocess.run(["git", "clone", "--depth", "1", repo_url, "-b", ref, test_dir.resolve()], check=True)
        if not test_dir.exists():
            raise RuntimeError("Failed to clone the project.")
        os.chdir(test_dir)

    if (test_dir / ".gitmodules").exists():
        subprocess.run(["git", "submodule", "update", "--init"], check=True)

    print(f"Current commit hash: {git_commit_hash()}")


def parse_solc_version(solc_version_string: str) -> str:
    solc_version_match = re.search(SOLC_FULL_VERSION_REGEX, solc_version_string)
    if solc_version_match is None:
        raise RuntimeError(f"Solc version could not be found in: {solc_version_string}.")
    return solc_version_match.group(1)


def get_solc_short_version(solc_full_version: str) -> str:
    solc_short_version_match = re.search(SOLC_SHORT_VERSION_REGEX, solc_full_version)
    if solc_short_version_match is None:
        raise RuntimeError(f"Error extracting short version string from: {solc_full_version}.")
    return solc_short_version_match.group(1)


def store_benchmark_report(self):
    raise NotImplementedError()


def replace_version_pragmas(test_dir: Path):
    """
    Replace fixed-version pragmas (part of Consensys best practice).
    Include all directories to also cover node dependencies.
    """
    print("Replacing fixed-version pragmas...")
    for source in test_dir.glob("**/*.sol"):
        content = source.read_text(encoding="utf-8")
        content = re.sub(r"pragma solidity [^;]+;", r"pragma solidity >=0.0;", content)
        with open(source, "w", encoding="utf-8") as f:
            f.write(content)


def run_test(runner: TestRunner):
    print(f"Testing {runner.config.name}...\n===========================")

    presets = runner.config.selected_presets()
    print(f"Selected settings presets: {' '.join(presets)}")

    # Configure solc compiler
    solc_version = runner.setup_solc()
    print(f"Using compiler version {solc_version}")

    # Download project
    download_project(runner.test_dir, runner.config.repo_url, runner.config.ref_type, runner.config.ref)

    # Configure run environment
    runner.setup_environment()

    # Configure TestRunner instance
    print(
        dedent(
            f"""\
        Configuring runner's profiles with:
        -------------------------------------
        Binary type: {runner.solc_binary_type}
        Compiler path: {runner.solc_binary_path}
        -------------------------------------
        """
        )
    )
    runner.compiler_settings(presets)
    for preset in runner.config.selected_presets():
        print("Running compile function...")
        settings = settings_from_preset(preset, runner.config.evm_version)
        print(
            dedent(
                f"""\
            -------------------------------------
            Settings preset: {preset}
            Settings: {settings}
            EVM version: {runner.config.evm_version}
            Compiler version: {get_solc_short_version(solc_version)}
            Compiler version (full): {solc_version}
            -------------------------------------
            """
            )
        )
        runner.compile(preset)
        # TODO: COMPILE_ONLY should be a command-line option
        if os.environ.get("COMPILE_ONLY") == "1" or preset in runner.config.compile_only_presets:
            print("Skipping test function...")
        else:
            print("Running test function...")
            runner.run_test()
        # TODO: store_benchmark_report # pylint: disable=fixme
    runner.clean()
    print("Done.")
