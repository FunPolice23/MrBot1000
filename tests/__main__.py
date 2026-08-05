#!/usr/bin/env python3
"""
MrBot1000 Test Suite Runner

Usage:
    python -m tests                    # Run all tests
    python -m tests --list               # List available tests
    python -m tests --test <name>        # Run specific test
    python -m tests --run <test1> <test2>  # Run multiple tests by name
    python -m tests --category <cat>     # Run all tests in a category
"""
import sys
import os
import json
import argparse
from datetime import datetime
from pathlib import Path

# Test categories and their test functions
TEST_CATEGORIES = {
    'syntax': {
        'description': 'Syntax validation tests',
        'tests': ['check_syntax']
    },
    'import': {
        'description': 'Import verification tests',
        'tests': ['test_imports', 'test_analyst_worker', 'test_job_search', 'test_main']
    },
    'health': {
        'description': 'Health and functionality tests',
        'tests': ['test_analyst_metrics', 'test_job_evaluation', 'test_coordinator']
    },
    'integration': {
        'description': 'Integration test bundles',
        'tests': ['run_full_suite', 'run_quick_check']
    }
}

# Get all individual test names (excluding bundle tests)
ALL_TESTS = [t for cat in TEST_CATEGORIES.values() for t in cat['tests'] if not t.startswith('run_')]

RESULTS_DIR = Path(__file__).parent / 'test_results'
RESULTS_FILE = RESULTS_DIR / f'test_run_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'

# Results storage
_results = {
    'timestamp': datetime.now().isoformat(),
    'tests_run': [],
    'passed': [],
    'failed': [],
    'errors': []
}


def ensure_results_dir():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def list_tests():
    print("Available test categories and tests:")
    print("=" * 60)
    for category, info in TEST_CATEGORIES.items():
        print(f"\n  [{category.upper()}] {info['description']}")
        for test in info['tests']:
            builtin = " (runs other tests)" if test.startswith('run_') else ""
            print(f"    • {test}{builtin}")
    print()
    print("Examples:")
    print("  python -m tests                    # Run all tests")
    print("  python -m tests --test check_syntax  # Run single test")
    print("  python -m tests --category syntax  # Run all syntax tests")
    print("  python -m tests --run check_syntax test_imports  # Run multiple tests")
    print()


def get_all_test_names():
    """Get all runnable test names (excluding bundle tests)"""
    return ALL_TESTS.copy() + ['run_full_suite', 'run_quick_check']


def run_test(test_name: str) -> tuple:
    """Run a single test by name. Returns (success, message)."""
    if test_name == 'check_syntax':
        return test_syntax()
    elif test_name == 'test_imports':
        return test_imports()
    elif test_name == 'test_analyst_worker':
        return test_analyst_worker()
    elif test_name == 'test_job_search':
        return test_job_search()
    elif test_name == 'test_main':
        return test_main()
    elif test_name == 'test_analyst_metrics':
        return test_analyst_metrics()
    elif test_name == 'test_job_evaluation':
        return test_job_evaluation()
    elif test_name == 'test_coordinator':
        return test_coordinator()
    elif test_name == 'run_quick_check':
        return run_tests(['check_syntax', 'test_imports', 'test_analyst_worker'])
    elif test_name == 'run_full_suite':
        return run_tests(ALL_TESTS)
    else:
        return False, f"Unknown test: {test_name}"


# ── Individual Test Functions ─────────────────────────────────────────────

def test_syntax() -> tuple:
    """Validate Python syntax for all modified files"""
    import py_compile
    
    files = [
        'D:/MrBot1000_2.0/manager.py',
        'D:/MrBot1000_2.0/main.py',
        'D:/MrBot1000_2.0/agents/analyst_worker.py',
        'D:/MrBot1000_2.0/agents/job_search_worker.py',
        'D:/MrBot1000_2.0/agents/coordinator.py',
        'D:/MrBot1000_2.0/agents/summarizer.py',
        'D:/MrBot1000_2.0/agents/base_worker.py',
    ]
    
    failed = []
    for f in files:
        try:
            py_compile.compile(f, doraise=True)
        except py_compile.PyCompileError as e:
            failed.append(f"{Path(f).name}: {e}")
    
    if failed:
        return False, f"Syntax errors:\n" + "\n".join(f"  ✗ {e}" for e in failed)
    return True, "All files passed syntax validation"


def test_imports() -> tuple:
    """Verify core modules can be imported"""
    sys.path.insert(0, 'D:/MrBot1000_2.0')
    
    try:
        import agents.base_worker
        from agents.job_search_worker import JobSearchWorker
        from agents.coordinator import CoordinatorWorker
    except Exception as e:
        return False, f"Import error: {e}"
    
    return True, "All core imports successful"


def test_main() -> tuple:
    """Verify main.py can be imported and ManagerThread has required methods"""
    sys.path.insert(0, 'D:/MrBot1000_2.0')
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'  # Avoid GUI issues
    
    try:
        if 'main' in sys.modules:
            del sys.modules['main']
        if 'manager' in sys.modules:
            del sys.modules['manager']
        
        from main import MainWindow
        from manager import ManagerThread
        
        # Verify required methods exist
        required_methods = ['set_summarizer', 'register_worker', 'get_free_worker']
        missing = [m for m in required_methods if not hasattr(ManagerThread, m)]
        
        if missing:
            return False, f"Missing methods: {', '.join(missing)}"
        
        return True, "MainWindow and ManagerThread imported successfully"
    except Exception as e:
        import traceback
        return False, f"Import error: {e}"


def test_analyst_worker() -> tuple:
    """Verify AnalystWorker can be instantiated"""
    sys.path.insert(0, 'D:/MrBot1000_2.0')
    
    try:
        from agents.analyst_worker import AnalystWorker
        worker = AnalystWorker(api_key="", log_signal=lambda x: None)
        return True, "AnalystWorker instantiated successfully"
    except Exception as e:
        return False, f"AnalystWorker error: {e}"


def test_job_search() -> tuple:
    """Verify JobSearchWorker can be instantiated"""
    sys.path.insert(0, 'D:/MrBot1000_2.0')
    
    try:
        from agents.job_search_worker import JobSearchWorker
        worker = JobSearchWorker(api_key="", log_signal=lambda x: None)
        return True, "JobSearchWorker instantiated successfully"
    except Exception as e:
        return False, f"JobSearchWorker error: {e}"


def test_analyst_metrics() -> tuple:
    """Test AnalystWorker proposal analysis functionality"""
    sys.path.insert(0, 'D:/MrBot1000_2.0')
    
    try:
        from agents.analyst_worker import AnalystWorker
        worker = AnalystWorker(api_key="", log_signal=lambda x: None)
        
        # Test proposal
        test_prop = "Requirements: Build chatbot. Deliverables: Code. Timeline: 4 weeks."
        metrics = worker.analyze_proposal(test_prop, "test")
        
        if not (0 <= metrics.quality_score <= 1):
            return False, f"Invalid quality score: {metrics.quality_score}"
        
        return True, f"Proposal analysis OK (quality={metrics.quality_score})"
    except Exception as e:
        return False, f"Analyst metrics error: {e}"


def test_job_evaluation() -> tuple:
    """Test job listing evaluation"""
    sys.path.insert(0, 'D:/MrBot1000_2.0')
    
    try:
        from agents.analyst_worker import AnalystWorker
        worker = AnalystWorker(api_key="", log_signal=lambda x: None)
        
        test_job = {
            "job_id": "test_job",
            "title": "Build AI Chatbot",
            "description": "Need Python dev",
            "budget": 500,
            "skills": ["Python"],
            "url": "https://example.com"
        }
        result = worker.evaluate_job_listing(test_job)
        
        if 'recommended_action' not in result:
            return False, "Missing recommended_action in result"
        
        return True, f"Job evaluation OK (action={result['recommended_action']})"
    except Exception as e:
        return False, f"Job evaluation error: {e}"


def test_coordinator() -> tuple:
    """Verify CoordinatorWorker functionality"""
    sys.path.insert(0, 'D:/MrBot1000_2.0')
    
    try:
        from agents.coordinator import CoordinatorWorker
        from agents.shared_context import SharedContext
        
        # Check SharedContext file exists
        sc_path = Path.home() / '.local/share/mrbot1000/shared_context.json'
        if not sc_path.exists():
            return True, "SharedContext file not yet created (will be created on first run)"
        
        return True, "Coordinator components OK"
    except Exception as e:
        return False, f"Coordinator error: {e}"


def run_tests(test_list: list) -> tuple:
    """Run multiple tests and collect results"""
    passed = []
    failed = []
    errors = []
    
    for test_name in test_list:
        success, msg = run_test(test_name)
        _results['tests_run'].append(test_name)
        
        if success:
            passed.append(test_name)
            print(f"  ✅ {test_name}: {msg}")
        else:
            failed.append(test_name)
            error_msg = f"{test_name}: {msg}"
            errors.append(error_msg)
            print(f"  ❌ {test_name}: {msg}")
    
    # Update results
    _results['passed'].extend(passed)
    _results['failed'].extend(failed)
    _results['errors'].extend(errors)
    
    if failed:
        return False, f"{len(failed)} test(s) failed: " + "; ".join(failed)
    return True, f"All {len(passed)} tests passed"


def run_category(category: str) -> tuple:
    """Run all tests in a category"""
    if category not in TEST_CATEGORIES:
        return False, f"Unknown category: {category}"
    
    tests = TEST_CATEGORIES[category]['tests']
    return run_tests(tests)


def save_results():
    """Save test results to JSON file"""
    ensure_results_dir()
    
    # Update timestamp for the save
    _results['timestamp'] = datetime.now().isoformat()
    
    with open(RESULTS_FILE, 'w') as f:
        json.dump(_results, f, indent=2)
    
    print(f"\n📁 Results saved to: {RESULTS_FILE}")
    print(f"   Summary: {len(_results['passed'])} passed, {len(_results['failed'])} failed")


def main():
    parser = argparse.ArgumentParser(description='MrBot1000 Test Suite')
    parser.add_argument('--list', action='store_true', help='List available tests')
    parser.add_argument('--test', type=str, help='Run specific test')
    parser.add_argument('--run', nargs='+', choices=get_all_test_names(),
                        help='Run specific tests by name (can specify multiple)')
    parser.add_argument('--category', type=str, choices=list(TEST_CATEGORIES.keys()),
                        help='Run all tests in a category')
    parser.add_argument('--all', action='store_true', help='Run all individual tests')
    
    args = parser.parse_args()
    
    ensure_results_dir()
    
    if args.list:
        list_tests()
        return 0
    
    # Determine which tests to run
    if args.test:
        tests_to_run = [args.test]
    elif args.run:
        tests_to_run = args.run
    elif args.category:
        tests_to_run = TEST_CATEGORIES[args.category]['tests']
    elif args.all:
        tests_to_run = ALL_TESTS
    else:
        # Default: run all individual tests
        tests_to_run = ALL_TESTS
    
    print(f"Running {len(tests_to_run)} test(s)...\n")
    
    success, msg = run_tests(tests_to_run)
    print(f"\n{msg}")
    
    save_results()
    
    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())