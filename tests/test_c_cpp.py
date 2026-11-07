import shutil
import gzip
import json
from pathlib import Path

import pytest

from crapper.analyze import analyze_files
from crapper.coverage import load_bundle
from crapper.discover import iter_source_files, language_of
from crapper.languages import functions_in_file
from crapper.runners import run_coverage
from crapper.native import collect_gcov


@pytest.mark.parametrize('suffix, language', [
    ('.c', 'c'), ('.h', 'cpp'), ('.cpp', 'cpp'), ('.cc', 'cpp'),
    ('.cxx', 'cpp'), ('.C', 'cpp'), ('.hpp', 'cpp'), ('.hh', 'cpp'),
    ('.hxx', 'cpp'), ('.H', 'cpp'), ('.ipp', 'cpp'), ('.tcc', 'cpp'),
])
def test_detects_c_and_cpp(suffix, language):
    assert language_of('source' + suffix) == language


def test_c_functions_decisions_and_function_pointer_return():
    source = '''int declared(int);
int *choose(int value) {
    if (value > 0 && value < 10) return 0;
    for (int i = 0; i < value; ++i) { while (value) --value; }
    do { ++value; } while (value < 0);
    switch (value) { case 1: break; default: break; }
    return value ? 0 : 0;
}
int (*factory(void))(int) { return 0; }
'''
    functions = functions_in_file('c', source, 'src/choose.c', '/project')
    assert [(fn.name, fn.complexity) for fn in functions] == [('choose', 9), ('factory', 1)]
    assert functions[0].namespace == 'src/choose.c'
    assert source.encode()[functions[0].start_byte:functions[0].end_byte].startswith(b'int *choose')


def test_cpp_scopes_constructors_operators_templates_and_nested_bodies():
    source = '''namespace outer::inner {
class Box {
public:
    Box() : value(1) {}
    ~Box() {}
    int get(int x) const { return x ? value : 0; }
    int operator()(int x) { return x; }
    operator bool() const { return true; }
    int value;
};
int Box::outside(int x) { return x; }
template<class T> T twice(T x) {
    auto fn = [](int n) { if (n) return 1; return 0; };
    struct Local { int hidden() { if (true) return 1; return 0; } };
    return x + x;
}
}
'''
    functions = functions_in_file('cpp', source, 'box.cpp', '/project')
    assert [(fn.namespace, fn.name) for fn in functions] == [
        ('outer::inner::Box', 'Box'), ('outer::inner::Box', '~Box'),
        ('outer::inner::Box', 'get'), ('outer::inner::Box', 'operator()'),
        ('outer::inner::Box', 'operator bool'), ('outer::inner::Box', 'outside'),
        ('outer::inner', 'twice'),
    ]
    assert functions[-1].complexity == 3


def test_headers_are_analyzed_and_tests_are_skipped(tmp_path):
    for name in ['main.c', 'inline.hpp', 'main_test.cpp', 'test_main.c']:
        (tmp_path / name).write_text('int f() { return 1; }\n')
    assert {p.name for p in iter_source_files([tmp_path])} == {'main.c', 'inline.hpp'}


def test_cpp_coverage_reads_existing_lcov(tmp_path):
    path = tmp_path / 'box.cpp'
    path.write_text('int choose(int x) {\n    if (x) return 1;\n    return 0;\n}\n')
    report = tmp_path / 'coverage/lcov.info'
    report.parent.mkdir()
    report.write_text(f'SF:{path}\nDA:1,1\nDA:2,1\nDA:3,0\nBRDA:2,0,0,1\nBRDA:2,0,1,0\nend_of_record\n')
    entries = analyze_files([path], tmp_path, load_bundle(tmp_path))
    assert entries[0].coverage == 50.0


def test_qualified_conversion_operator_keeps_its_type_name():
    source = 'namespace demo { struct Box { operator std::string() const { return ""; } }; }'
    functions = functions_in_file('cpp', source, 'box.hpp', '/project')
    assert [(fn.namespace, fn.name) for fn in functions] == [('demo::Box', 'operator std::string')]


def test_preprocessor_conditions_are_not_runtime_decisions():
    source = 'int f(int x) {\n#if A && B\n if (x) return 1;\n#endif\n return 0;\n}\n'
    assert functions_in_file('c', source, 'a.c', '/project')[0].complexity == 2


def test_missing_build_config_does_not_reuse_a_stale_native_report(tmp_path):
    report = tmp_path / 'target/coverage/cpp/lcov.info'
    report.parent.mkdir(parents=True)
    report.write_text('Old coverage')
    run_coverage(tmp_path, [tmp_path / 'a.cpp'], None)
    assert not report.exists()


def test_header_coverage_is_merged_across_translation_units(tmp_path):
    header = tmp_path / 'value.hpp'
    header.write_text('inline int choose(int n) {\n    return n > 0 ? 1 : 0;\n}\n')
    build = tmp_path / 'build'
    build.mkdir()
    for name in ['left', 'right']:
        (build / (name + '.gcno')).touch()
    report = tmp_path / 'coverage/lcov.info'
    report.parent.mkdir()

    def fake_gcov(command, cwd):
        left = Path(command[-1]).stem == 'left'
        data = {'current_working_directory': str(tmp_path), 'files': [{
            'file': 'value.hpp', 'lines': [{
                'line_number': 2, 'count': 1,
                'branches': [{'count': int(left)}, {'count': int(not left)}],
            }],
        }]}
        with gzip.open(cwd / 'value.gcov.json.gz', 'wt') as stream:
            json.dump(data, stream)
        return 0

    collect_gcov(build, report, fake_gcov, lambda message: pytest.fail(message))
    entry = analyze_files([header], tmp_path, load_bundle(tmp_path))[0]
    assert entry.coverage == 100.0


@pytest.mark.skipif(not all(shutil.which(x) for x in ['gcc', 'g++', 'gcov', 'cmake', 'ctest']), reason='Needs GCC and CMake')
def test_default_coverage_builds_and_runs_a_mixed_c_cpp_project(tmp_path):
    (tmp_path / 'CMakeLists.txt').write_text('''cmake_minimum_required(VERSION 3.16)
project(sample LANGUAGES C CXX)
enable_testing()
add_executable(sample main.cpp value.c)
add_test(NAME sample COMMAND sample)
''')
    (tmp_path / 'value.c').write_text('int value(int x) {\n    if (x > 0) return 7;\n    return 0;\n}\n')
    (tmp_path / 'main.cpp').write_text('extern "C" int value(int);\nint main() { return value(1) == 7 ? 0 : 1; }\n')
    paths = [tmp_path / 'value.c', tmp_path / 'main.cpp']
    assert run_coverage(tmp_path, paths, None) == 0
    entries = analyze_files(paths, tmp_path, load_bundle(tmp_path))
    assert len(entries) == 2
    assert all(entry.coverage and entry.coverage > 0 for entry in entries)
    assert not (tmp_path / 'build').exists()
