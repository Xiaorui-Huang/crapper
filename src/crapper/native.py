"""Покрытие C/C++: отдельная сборка CMake/GCC и преобразование gcov JSON в LCOV."""

import gzip
import json
from pathlib import Path


def _line_records(lines):
    records = []
    for line in lines:
        number = line['line_number']
        records.append(f"DA:{number},{line['count']}")
        for index, branch in enumerate(line.get('branches', [])):
            records.append(f"BRDA:{number},0,{index},{branch['count']}")
    return records


def _merge_line(existing, incoming):
    existing['count'] += incoming['count']
    branches = existing.setdefault('branches', [])
    for index, branch in enumerate(incoming.get('branches', [])):
        if index < len(branches):
            branches[index]['count'] += branch['count']
        else:
            branches.append(dict(branch))


def _read_gcov(paths):
    files = {}
    for path in paths:
        with gzip.open(path, 'rt', encoding='utf-8') as stream:
            report = json.load(stream)
        cwd = Path(report.get('current_working_directory', '.'))
        for entry in report.get('files', []):
            source = str((cwd / entry['file']).resolve())
            lines = files.setdefault(source, {})
            for line in entry.get('lines', []):
                number = line['line_number']
                if number in lines:
                    _merge_line(lines[number], line)
                else:
                    lines[number] = line
    return files


def _write_lcov(files, report):
    records = []
    for source, lines in sorted(files.items()):
        records.append(f'SF:{source}')
        records.extend(_line_records([lines[key] for key in sorted(lines)]))
        records.append('end_of_record')
    report.write_text('\n'.join(records) + '\n', encoding='utf-8')


def collect_gcov(build, report, execute, warn):
    notes = sorted(build.rglob('*.gcno'))
    if not notes:
        warn('GCC wrote no .gcno files; check the compiler and the --coverage flags.')
        return 1
    snapshots = []
    code = 0
    for index, note in enumerate(notes):
        directory = report.parent / 'gcov' / str(index)
        directory.mkdir(parents=True, exist_ok=True)
        if execute(['gcov', '--json-format', '--branch-probabilities', str(note)], directory) != 0:
            warn(f'Could not read coverage for {note}')
            code = 1
            continue
        snapshots.extend(directory.glob('*.gcov.json.gz'))
    _write_lcov(_read_gcov(snapshots), report)
    return code


def run_native_coverage(root, execute, warn):
    """Build with --coverage, run ctest, and write LCOV. Returns 0 on success, else non-zero."""

    report = root / 'target/coverage/cpp/lcov.info'
    report.unlink(missing_ok=True)
    if not (root / 'CMakeLists.txt').is_file():
        warn('C/C++ coverage needs a CMakeLists.txt in --root, or a --coverage-command that writes an LCOV report.')
        return 1
    build = report.parent / 'build'
    build.mkdir(parents=True, exist_ok=True)
    for path in build.rglob('*.gcda'):
        path.unlink()
    flags = '--coverage -O0 -g'
    commands = [
        ['cmake', '-S', str(root), '-B', str(build),
         '-DCMAKE_BUILD_TYPE=Debug', '-DBUILD_TESTING=ON',
         f'-DCMAKE_C_FLAGS={flags}', f'-DCMAKE_CXX_FLAGS={flags}',
         '-DCMAKE_EXE_LINKER_FLAGS=--coverage', '-DCMAKE_SHARED_LINKER_FLAGS=--coverage'],
        ['cmake', '--build', str(build), '--config', 'Debug'],
        ['ctest', '--test-dir', str(build), '-C', 'Debug', '--output-on-failure', '--no-tests=error'],
    ]
    for command in commands:
        if execute(command, root) != 0:
            warn('The C/C++ build or tests failed; coverage was not updated.')
            return 1
    return collect_gcov(build, report, execute, warn)
