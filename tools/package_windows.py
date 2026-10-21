"""Package tested Windows output with matching committed source and licenses."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    binary = args.binary.resolve()
    executable = binary/'LingXi.exe'
    report = json.loads(args.report.read_text(encoding='utf-8'))
    if not report.get('ok') or not report.get('frozen') or report.get('executableSHA256') != digest(executable):
        raise SystemExit('Frozen EXE report must pass and match this exact binary.')
    version = report.get('version', '')
    if not re.fullmatch(r'[A-Za-z0-9.-]+', version):
        raise SystemExit('Invalid version')
    for command in (['diff', '--quiet'], ['diff', '--cached', '--quiet']):
        if subprocess.run(['git', *command], cwd=root).returncode:
            raise SystemExit('Commit source changes before packaging.')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    files = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode('utf-8').strip('\0').split('\0')
    forbidden = {'.pfx', '.p12', '.key', '.exe', '.avi', '.pyc'}
    manifest = []
    for name in files:
        path = root/name
        if path.suffix.lower() in forbidden or any(part in ('userdata', 'frames', '.venv', 'dist', 'release') for part in path.relative_to(root).parts):
            raise SystemExit(f'Unexpected private/generated tracked file: {name}')
        manifest.append({'path': name, 'sha256': digest(path)})
    provenance = {'version': version, 'sourceCommit': commit, 'executableSHA256': digest(executable), 'files': manifest}
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit('Choose an empty output directory')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='lingxi-package-') as temporary:
        portable = Path(temporary)/'LingXi'
        shutil.copytree(binary, portable, ignore=shutil.ignore_patterns('userdata', 'frames', '*.log', 'settings.json', 'desktop.json'))
        for name in ('README.md', 'LICENSE', 'NOTICE.md', 'CHANGELOG.md'):
            shutil.copyfile(root/name, portable/name)
        shutil.copytree(root/'docs', portable/'docs')
        shutil.copyfile(args.report, portable/'SELF_TEST.json')
        (portable/'RELEASE_SOURCE.json').write_text(json.dumps(provenance, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        notices = portable/'ThirdPartyLicenses'
        notices.mkdir()
        shutil.copyfile(Path(sys.base_prefix)/'LICENSE.txt', notices/'Python-LICENSE.txt')
        for metadata in (Path(sys.prefix)/'Lib'/'site-packages').glob('*.dist-info'):
            for path in metadata.rglob('*'):
                if path.is_file() and any(key in path.name.lower() for key in ('license', 'copying', 'notice')):
                    target = notices/metadata.name/path.relative_to(metadata)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, target)
        source_zip = output/f'LingXi-{version}-source.zip'
        with zipfile.ZipFile(source_zip, 'w', zipfile.ZIP_DEFLATED) as archive:
            for item in manifest:
                archive.write(root/item['path'], 'LingXi-source/'+item['path'])
            archive.writestr('LingXi-source/RELEASE_SOURCE.json', json.dumps(provenance, ensure_ascii=False, indent=2)+'\n')
        portable_zip = output/f'LingXi-{version}-windows-x64.zip'
        with zipfile.ZipFile(portable_zip, 'w', zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(portable.rglob('*')):
                if path.is_file():
                    archive.write(path, path.relative_to(Path(temporary)))
        for path in (portable_zip, source_zip):
            with zipfile.ZipFile(path) as archive:
                if archive.testzip():
                    raise SystemExit(f'ZIP CRC failed: {path.name}')
        sums = [(digest(executable), 'LingXi/LingXi.exe')]+[(digest(p), p.name) for p in (portable_zip, source_zip)]
        (output/'SHA256SUMS.txt').write_text(''.join(f'{sha}  {name}\n' for sha, name in sums), encoding='utf-8')
        print(json.dumps({'commit': commit, 'version': version, 'artifacts': dict((name, sha) for sha, name in sums)}, indent=2))


if __name__ == '__main__':
    main()
