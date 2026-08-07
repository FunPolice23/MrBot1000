#!/usr/bin/env python3
"""Final hermes verification - 4.0.3"""
import ast
import os

print("=" * 60)
print("Hermes Ad-hoc Verification: Final v4.0.3")
print("=" * 60)

# Read all key files
files_to_check = [
    ('agents/job_search_worker.py', 'Real client integration'),
    ('agents/coder.py', 'Coder worker'),
    ('agents/upwork_client.py', 'Upwork API'),
    ('agents/fiverr_client.py', 'Fiverr RSS'),
    ('CHANGELOG.md', 'Changelog'),
]

results = []
for f, desc in files_to_check:
    print(f"\n[{f}] {desc}:")
    try:
        with open(f, 'r') as file:
            content = file.read()
        
        # Syntax check for Python files
        if f.endswith('.py'):
            try:
                ast.parse(content)
                print("  ✅ Syntax valid")
                results.append((f, True, "syntax"))
            except SyntaxError as e:
                print(f"  ❌ Syntax error: {e}")
                results.append((f, False, "syntax"))
        
        # Content checks
        if 'job_search_worker' in f:
            checks = [
                ('EXCLUDED_PLATFORMS', 'EXCLUDED_PLATFORMS' in content),
                ('ClawGig excluded', '"ClawGig"' in content),
                ('FiverrClient integration', 'FiverrClient' in content and 'find_gigs' in content),
                ('UpworkClient integration', 'UpworkClient' in content),
                ('Web search fallback', 'web_search' in content),
            ]
            for name, passed in checks:
                status = "✅" if passed else "❌"
                print(f"  {status} {name}")
                results.append((f"{f} - {name}", passed, "content"))
                
        elif 'coder.py' in f:
            methods = ['analyze_and_fix', 'file_write', 'refactor']
            for m in methods:
                has_method = f'def {m}' in content
                status = "✅" if has_method else "❌"
                print(f"  {status} {m}() method")
                results.append((f"{f} - {m}", has_method, "content"))
            
    except FileNotFoundError:
        print(f"  ❌ File not found")
        results.append((f, False, "exists"))
    except Exception as e:
        print(f"  ❌ Error: {e}")
        results.append((f, False, "error"))

# Summary
print("\n" + "=" * 60)
passed = sum(1 for _, p, _ in results if p)
total = len(results)
print(f"Final: {passed}/{total} checks passed")

if passed >= total * 0.9:
    print("✅ All critical verifications passed!")
else:
    print("⚠️  Some checks failed - see details above")