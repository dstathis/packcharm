#!/usr/bin/env python3

import argparse
import ast
import pathlib
import shutil
import subprocess
import sys
import time
import typing
import yaml

MANIFEST_YAML = """analysis:
  attributes:
  - name: language
    result: python
  - name: framework
    result: operator
bases:
- architectures:
  - amd64
  channel: '22.04'
  name: ubuntu
charmcraft-started-at: '2022-03-23T23:38:04.944491Z'
charmcraft-version: 1.5.0
"""

DISPATCH = """#!/bin/sh

# Customized version of the dispatch script that adds "." to PYTHONPATH
# so that the charm libraries in /src/main/charm_libs can also be loaded
JUJU_DISPATCH_PATH="${JUJU_DISPATCH_PATH:-$0}" PYTHONPATH=lib:venv:. ./src/charm.py
"""

COPY_IGNORES = ['.git', '*.charm', 'packcharm-*', 'venv']
if pathlib.Path('.gitignore').is_file():
    with pathlib.Path('.gitignore').open() as f:
        extras = f.read().splitlines()
    COPY_IGNORES += extras
    while 'metadata.yaml' in COPY_IGNORES:
        COPY_IGNORES.remove('metadata.yaml')

VERBOSE = None


def run(cmd, shell=False, fail_ok=False, env=None):
    if VERBOSE:
        print(f'+ {cmd}')
    if not shell:
        cmd = cmd.split()
    if fail_ok:
        subprocess.call(cmd, shell=shell, env=env)
    else:
        subprocess.check_call(cmd, shell=shell, env=env)


def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--clean', action='store_true', help='Rebuild the cache directory')
    parser.add_argument('-o', '--output', default='./my.charm', type=pathlib.Path, help='Path of charm file to create')
    parser.add_argument('--keep-temp-dir', action='store_true', help='Keep temporary build directory')
    parser.add_argument('-v', '--verbose', action='store_true', default=False, help='Verbose output')
    return parser.parse_args()


def check_installed(pkg):
    p = subprocess.run(['dpkg', '-s', pkg])
    if p.returncode == 0:
        return True
    return False


def get_pydeps(libdir):
    pydeps = []
    for pyfile in libdir.glob('**/*.py'):
        with pyfile.open() as f:
            tree = ast.parse(f.read())
        for node in tree.body:
            if type(node) == ast.Assign and node.targets[0].id == 'PYDEPS':
                pydeps += ast.literal_eval(node.value)
    if not pydeps:
        return ''
    pydeps = '" "'.join(pydeps)
    pydeps = f'"{pydeps}"'
    return pydeps


def pack(
    charm_root: typing.Union[str, pathlib.Path],
    clean: bool = False,
    output_file: typing.Union[str, pathlib.Path] = './my.charm',
    keep: bool = False,
    verbose: bool = False,
):
    '''Pack a charm.

    Args:
        charm_root: Root of the charm directory.
        clean: Delete the cached python packages before packing.
        output_file: Name of the charm file to output.
        keep: Keep the temporary charm directory.
        verbose: Verbose output.
    '''
    output_file = pathlib.Path(output_file)
    global VERBOSE
    VERBOSE = verbose
    if not pathlib.Path('./metadata.yaml').is_file():
        sys.stderr.write('Please run from inside a charm directory\n')
        sys.stderr.flush()
        sys.exit(1)
    try:
        subprocess.check_call(['which', 'zip'])
    except subprocess.CalledProcessError:
        sys.stderr.write('Please install zip\n')
        sys.stderr.flush()
        sys.exit(1)
    home = pathlib.Path.home()
    wd = pathlib.Path.cwd()
    cache = home / f'.packcharm/{wd.name}'
    if clean and cache.exists():
        shutil.rmtree(cache)
    cache.mkdir(parents=True, exist_ok=True)
    if not (cache / 'venv').exists():
        run(f'virtualenv {cache / "venv"}')
    tempdir = pathlib.Path(f'./packcharm-{time.time()}')
    tempdir.mkdir()

    # parts
    with (wd / 'charmcraft.yaml').open() as f:
        charmcraft = yaml.safe_load(f)
    for part_key in charmcraft.get('parts', []):
        part = charmcraft['parts'][part_key]
        for pkg in part['build-packages']:
            check_installed(pkg)
        if part.get('plugin') == 'dump':
            run(part['override-pull'], shell=True, env={'CRAFT_TARGET_ARCH': 'amd64'})
            shutil.copytree(part['source'], tempdir, dirs_exist_ok=True, ignore=shutil.ignore_patterns(*COPY_IGNORES))

    # copy charm in to tempdir
    shutil.copytree('.', tempdir, dirs_exist_ok=True, ignore=shutil.ignore_patterns(*COPY_IGNORES))

    # create special files
    with (tempdir / 'manifest.yaml').open('w') as f:
        f.write(MANIFEST_YAML)
    with (tempdir / 'dispatch').open('w') as f:
        f.write(DISPATCH)
    run(f'chmod +x {tempdir}/dispatch')

    pip_install_cmd = "pip install --upgrade --upgrade-strategy eager"

    # install python deps
    run(f'. {cache / "venv/bin/activate"}; {pip_install_cmd} -r {tempdir}/requirements.txt', shell=True)
    pydeps = get_pydeps(wd / 'lib')
    if pydeps:
        run(f'. {cache / "venv/bin/activate"}; {pip_install_cmd} {pydeps}', shell=True)
    (tempdir / 'venv').mkdir()
    run(f'cp -r {cache}/venv/lib/python3.*/site-packages/* {tempdir}/venv/', shell=True)

    # zip charm
    run(f'cd {tempdir};zip -1 -r -q {output_file.resolve()} ./*;cd {wd}', shell=True)
    if not keep:
        shutil.rmtree(tempdir)

    print('Done!')
    return output_file


def main():
    args = get_args()
    pack(charm_root='.', clean=args.clean, output_file=args.output, keep=args.keep_temp_dir, verbose=args.verbose)
