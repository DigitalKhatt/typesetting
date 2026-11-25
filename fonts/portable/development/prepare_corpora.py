#!/usr/bin/env python3
"""Export the original Mushaf text and line map used by the portable font tasks."""
import argparse
from datetime import datetime, timezone
from pathlib import Path

from tools.compare_baselines import compare_baselines as live
from tools.font_validation import validate

from .project import CORPORA_DIR, FAMILIES, VALIDATION_DIR, WORKSPACE
from .validation.portable_checks import corpus_run


def export_family(key, root, profiles, renderer):
    family = root / key
    run = family / 'runs' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    run.mkdir(parents=True)
    profile = run / 'profile.json'
    live.save(profile, validate.live_settings(profiles['fonts'], key, 'unjustified'))
    options = live.settings(argparse.Namespace(workspace=WORKSPACE, config=profile, last_page=None), key, run)
    # The shared OpenType corpus export disables Tajweed. The collision checker
    # applies the family's GUI Tajweed setting when it shapes this original text.
    options.update(force=False, tajweed=False)
    request = run / 'corpus-request.settings.json'
    live.save(request, options)
    live.export_run(renderer, request, run, WORKSPACE, run / 'corpus.bin')
    pages = live.load(run / 'corpus.bin.json')['pages']
    locations = [dict(page=p, line=n, text=text) for p, page in enumerate(pages, 1)
                 for n, text in enumerate(page, 1)]
    if not locations:
        raise ValueError(f'{key}: the native renderer exported an empty corpus')
    (run / 'corpus.txt').write_text(''.join(row['text'] + '\n' for row in locations), encoding='utf-8')
    live.save(run / 'locations.json', locations)
    live.save(run / 'engine-settings.json',
              dict(direction='rtl', script='arab', language='ar', size=1000, clusterLevels=[0, 1],
                   directwriteCGJ=profiles['directwriteCGJ'], coretextCGJ=profiles['coretextCGJ']))
    # Readers keep their immutable run. Publish the new pointer only after export succeeds.
    validate.save(family / 'latest.json', dict(run=str(run.relative_to(family))))
    print(f'{key}: exported {len(locations):,} lines from {options["layout"]} to {run}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=CORPORA_DIR)
    parser.add_argument('--font', action='append', choices=FAMILIES, help='Repeat to select families (default: all)')
    parser.add_argument('--if-missing', action='store_true', help='Reuse each complete local export when available')
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    profiles = live.load(VALIDATION_DIR / 'config/profiles.json')
    renderer = live.renderer_path(WORKSPACE / profiles['buildDir'], profiles['configuration'])
    with live.run_lock(root / '.export.lock'):
        for key in dict.fromkeys(args.font or FAMILIES):
            if args.if_missing:
                try:
                    existing = corpus_run(key, profiles['directWriteVersion'], root)
                except FileNotFoundError:
                    pass
                else:
                    print(f'{key}: reusing {existing}', flush=True)
                    continue
            print(f'{key}: exporting the full original corpus', flush=True)
            export_family(key, root, profiles, renderer)


if __name__ == '__main__':
    main()
