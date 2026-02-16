#!/usr/bin/env python3
"""
Display SQL migrations for manual execution in Supabase.
"""
from pathlib import Path

def show_migrations():
    """Display all SQL migrations in order."""
    print("🛠️  SQL Migrations for Existing Schema")
    print("=" * 60)
    print("Copy and paste these SQL scripts into your Supabase SQL Editor")
    print("Run them in order (1, 2, 3)")
    
    # Get the project root
    project_root = Path(__file__).parent.parent
    sql_dir = project_root / "sql"
    
    # Migration files in order
    migration_files = [
        "20250202000000_add_missing_columns.sql",
        "20250202000001_create_rpc_functions_existing.sql", 
        "20250202000002_create_monitoring_views_existing.sql"
    ]
    
    for i, filename in enumerate(migration_files, 1):
        filepath = sql_dir / filename
        
        if filepath.exists():
            print(f"\n{'=' * 60}")
            print(f"MIGRATION {i}: {filename.replace('.sql', '').replace('_', ' ').title()}")
            print(f"File: {filepath.relative_to(project_root)}")
            print(f"{'=' * 60}")
            
            with open(filepath, 'r') as f:
                content = f.read()
                print(content)
            
            print(f"\n{'=' * 60}")
            print(f"END OF MIGRATION {i}")
            print(f"{'=' * 60}")
        else:
            print(f"\n⚠️  Migration file not found: {filename}")
    
    print("\n🎉 After running all migrations, test with:")
    print("   python3 scripts/test_supabase.py")

if __name__ == "__main__":
    show_migrations() 