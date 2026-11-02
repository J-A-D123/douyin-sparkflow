"""Build a deterministic source ZIP after the local credential check passes."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_check', ROOT / 'deploy/check-release.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def build_bundle(root, output):
    files = [name for name in check.candidate_files(root) if (root / name).is_file()]
    findings = check.check_files(root, files, check.local_secrets(root))
    if findings:
        raise RuntimeError('Source check failed: ' + json.dumps(findings, ensure_ascii=False))
    if output.resolve().is_relative_to(root.resolve()) and not output.resolve().is_relative_to((root / 'output').resolve()):
        raise ValueError('Inside the repository, write packages only under ignored output/')
    payloads = {name: (root / name).read_bytes() for name in files}
    # Recheck the exact bytes being archived, rather than trusting a prior scan.
    secrets = check.local_secrets(root)
    if any(any(pattern.search(data) for pattern in check.SECRET_PATTERNS) or any(secret in data for secret in secrets) for data in payloads.values()):
        raise RuntimeError('Source changed during packaging; rerun the source check')
    manifest = {'schemaVersion': 1, 'files': {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()}}
    payloads['SOURCE_MANIFEST.json'] = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + '.tmp')
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(payloads.items()):
                info = zipfile.ZipInfo('SparkFlow/' + name, date_time=(1980, 1, 1, 0, 0, 0))
                mode = 0o755 if name != 'SOURCE_MANIFEST.json' and os.access(root / name, os.X_OK) else 0o644
                info.external_attr = (0o100000 | mode) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    return {'sourceFiles': len(files), 'sha256': digest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'output/SparkFlow-source.zip')
    args = parser.parse_args()
    print(json.dumps(build_bundle(ROOT, args.output), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
