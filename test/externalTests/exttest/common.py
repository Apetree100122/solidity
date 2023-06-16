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
import sys
import mimetypes
from pathlib import Path
from tempfile import TemporaryDirectory

import subprocess
from shutil import which, copyfile, copytree, rmtree
from argparse import ArgumentParser

from dataclasses import dataclass, field
from typing import List

import re
from abc import ABCMeta, abstractmethod

# Our scripts/ is not a proper Python package so we need to modify PYTHONPATH to import from it
# pragma pylint: disable=import-error,wrong-import-position
PROJECT_ROOT = Path(__file__).parents[3]
sys.path.insert(0, f"{PROJECT_ROOT}/scripts")

from common.git_helpers import run_git_command, git_commit_hash

SOLC_FULL_VERSION_REGEX = re.compile(r"^[a-zA-Z: ]*(.*)$")
SOLC_SHORT_VERSION_REGEX = re.compile(r"^([0-9.]+).*\+|\-$")

CURRENT_EVM_VERSION: str = "london"
AVAILABLE_PRESETS: List[str] = [
    "legacy-no-optimize",
    "ir-no-optimize",
    "legacy-optimize-evm-only",
    "ir-optimize-evm-only",
    "legacy-optimize-evm+yul",
    "ir-optimize-evm+yul",
]


@dataclass
class SolcConfig:
    binary_type: str = field(default="native")
    binary_path: str = field(default="/usr/local/bin/solc")
    branch: str = field(default="master")
    install_dir: str = field(default="solc")
    solcjs_src_dir: str = field(default="")


@dataclass
class TestConfig:
    repo_url: str
    ref_type: str
    ref: str
    build_dependency: str = field(default="nodejs")
    compile_only_presets: List[str] = field(default_factory=list)
    settings_presets: List[str] = field(default_factory=lambda: AVAILABLE_PRESETS)
    evm_version: str = field(default=CURRENT_EVM_VERSION)
    solc: SolcConfig = field(default_factory=SolcConfig)

    def selected_presets(self):
        return set(self.compile_only_presets + self.settings_presets)


class InvalidConfigError(Exception):
    pass


class WrongBinaryType(Exception):
    pass


class TestRunner(metaclass=ABCMeta):
    config: TestConfig

    def __init__(self, config: TestConfig):
        if config.solc.binary_type not in ("native", "solcjs"):
            raise InvalidConfigError(
                f"Invalid solidity compiler binary type: {config.solc.binary_type}"
            )
        if config.solc.binary_type != "solcjs" and config.solc.solcjs_src_dir != "":
            raise InvalidConfigError(
                f"""Invalid test configuration: 'native' mode cannot be used with 'solcjs_src_dir'.
                Please use 'binary_type: solcjs' or unset: 'solcjs_src_dir: {config.solc.solcjs_src_dir}'"""
            )
        self.config = config

    @staticmethod
    def on_local_test_dir(fn):
        """Run a function inside the test directory"""

        def f(self, *args, **kwargs):
            if self.test_dir is None:
                raise InvalidConfigError("Test directory not defined")

            os.chdir(self.test_dir)
            return fn(self, *args, **kwargs)

        return f

    @abstractmethod
    def setup_environment(self, test_dir: Path):
        pass

    @abstractmethod
    def clean(self):
        pass

    @abstractmethod
    def compiler_settings(self, solc_version: str, presets: List[str]):
        pass

    @abstractmethod
    def compile(self, solc_version: str, preset: str):
        pass

    @abstractmethod
    def run_test(self, preset: str):
        pass


# Helper functions
def compiler_settings(evm_version, via_ir="false", optimizer="false", yul="false") -> dict:
    return {
        "optimizer": {"enabled": optimizer, "details": {"yul": yul}},
        "evmVersion": evm_version,
        "viaIR": via_ir,
    }


def settings_from_preset(preset: str, evm_version: str) -> dict:
    if preset not in AVAILABLE_PRESETS:
        raise InvalidConfigError(
            f'Preset "{preset}" not found.\n'
            "Please select one or more of the available presets: " +
            " ".join(AVAILABLE_PRESETS) + "\n"
        )
    switch = {
        "legacy-no-optimize": compiler_settings(evm_version),
        "ir-no-optimize": compiler_settings(evm_version, via_ir="true"),
        "legacy-optimize-evm-only": compiler_settings(evm_version, optimizer="true"),
        "ir-optimize-evm-only": compiler_settings(evm_version, via_ir="true", optimizer="true"),
        "legacy-optimize-evm+yul": compiler_settings(evm_version, optimizer="true", yul="true"),
        "ir-optimize-evm+yul":
            compiler_settings(evm_version, via_ir="true", optimizer="true", yul="true"),
    }
    assert preset in switch
    return switch[preset]


def parse_command_line(description: str, args: List[str]):
    arg_parser = ArgumentParser(description)
    arg_parser.add_argument(
        "solc_binary_type",
        metavar="solc-binary-type",
        type=str,
        help="""Solidity compiler binary type""",
        choices=["native", "solcjs"],
    )
    arg_parser.add_argument(
        "solc_binary_path",
        metavar="solc-binary-path",
        type=str,
        help="""Path to solc or soljson.js binary""",
    )
    return arg_parser.parse_args(args)


def download_project(test_dir: Path, repo_url: str, ref_type: str = "branch", ref: str = "master"):
    assert ref_type in ("commit", "branch", "tag")

    print(f"Cloning {ref_type} {ref} of {repo_url}...")
    if ref_type == "commit":
        os.mkdir(test_dir)
        os.chdir(test_dir)
        run_git_command(["git", "init"])
        run_git_command(["git", "remote", "add", "origin", repo_url])
        run_git_command(["git", "fetch", "--depth", "1", "origin", ref])
        run_git_command(["git", "reset", "--hard", "FETCH_HEAD"])
    else:
        os.chdir(test_dir.parent)
        run_git_command(["git", "clone", "--depth", "1", repo_url, "-b", ref, test_dir.resolve()])
        if not test_dir.exists():
            raise RuntimeError("Failed to clone the project.")
        os.chdir(test_dir)

    if (test_dir / ".gitmodules").exists():
        run_git_command(["git", "submodule", "update", "--init"])

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


def setup_solc(config: TestConfig, test_dir: Path) -> str:
    if config.solc.binary_type == "solcjs":
        solc_dir = test_dir.parent / config.solc.install_dir
        solc_js_entry_point = solc_dir / "dist/solc.js"

        print("Setting up solc-js...")
        if config.solc.solcjs_src_dir == "":
            download_project(
                solc_dir,
                "https://github.com/ethereum/solc-js.git",
                "branch",
                config.solc.branch,
            )
        else:
            print(f"Using local solc-js from {config.solc.solcjs_src_dir}...")
            copytree(config.solc.solcjs_src_dir, solc_dir)
            rmtree(solc_dir / "dist")
            rmtree(solc_dir / "node_modules")
        os.chdir(solc_dir)
        subprocess.run(["npm", "install"], check=True)
        subprocess.run(["npm", "run", "build"], check=True)

        if mimetypes.guess_type(config.solc.binary_path)[0] != "application/javascript":
            raise WrongBinaryType(
                "Provided soljson.js is expected to be of the type application/javascript but it is not."
            )

        copyfile(config.solc.binary_path, solc_dir / "dist/soljson.js")
        solc_version_output = subprocess.getoutput(f"node {solc_js_entry_point} --version")
    else:
        print("Setting up solc...")
        solc_version_output = subprocess.getoutput(
            f"{config.solc.binary_path} --version"
        ).split(":")[1]

    return parse_solc_version(solc_version_output)


def store_benchmark_report(self):
    raise NotImplementedError()


def prepare_node_env(test_dir: Path):
    if which("node") is None:
        raise RuntimeError("nodejs not found.")
    # Remove lock files (if they exist) to prevent them from overriding
    # our changes in package.json
    print("Removing package lock files...")
    rmtree(test_dir / "yarn.lock", ignore_errors=True)
    rmtree(test_dir / "package_lock.json", ignore_errors=True)

    print("Disabling package.json hooks...")
    package_json_path = test_dir / "package.json"
    if not package_json_path.exists():
        raise FileNotFoundError("package.json not found.")
    package_json = package_json_path.read_text(encoding="utf-8")
    package_json = re.sub(r'("prepublish":)\s".+"', lambda m: f'{m.group(1)} ""', package_json)
    package_json = re.sub(r'("prepare":)\s".+"', lambda m: f'{m.group(1)} ""', package_json)
    with open(package_json_path, "w", encoding="utf-8") as f:
        f.write(package_json)


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


def run_test(name: str, runner: TestRunner):
    print(f"Testing {name}...\n===========================")
    with TemporaryDirectory(prefix=f"ext-test-{name}-") as tmp_dir:
        test_dir = Path(tmp_dir) / "ext"
        presets = runner.config.selected_presets()
        print(f"Selected settings presets: {' '.join(presets)}")

        # Configure solc compiler
        solc_version = setup_solc(runner.config, test_dir)
        print(f"Using compiler version {solc_version}")

        # Download project
        download_project(test_dir, runner.config.repo_url, runner.config.ref_type, runner.config.ref)

        # Configure run environment
        if runner.config.build_dependency == "nodejs":
            prepare_node_env(test_dir)
        runner.setup_environment(test_dir)

        replace_version_pragmas(test_dir)
        # Configure TestRunner instance
        runner.compiler_settings(solc_version, presets)
        for preset in runner.config.selected_presets():
            print("Running compile function...")
            runner.compile(solc_version, preset)
            if (
                os.environ.get("COMPILE_ONLY") == "1"
                or preset in runner.config.compile_only_presets
            ):
                print("Skipping test function...")
            else:
                print("Running test function...")
                runner.run_test(preset)
            # TODO: store_benchmark_report # pylint: disable=fixme
            # runner.clean()
        print("Done.")