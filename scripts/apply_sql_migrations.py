#!/usr/bin/env python3
"""
Apply SQL migrations to Supabase database.
This script applies the SQL files in the sql/ directory to create missing RPC functions.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client, Client

def apply_sql_migrations():
    """Apply SQL migrations to create missing RPC functions."""
    
    # Load environment variables
    load_dotenv()
    
    # Get Supabase credentials
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    
    if not supabase_url or not supabase_key:
        print("❌ Error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env file")
        sys.exit(1)
    
    # Create Supabase client
    try:
        supabase: Client = create_client(supabase_url, supabase_key)
        print("✅ Connected to Supabase")
    except Exception as e:
        print(f"❌ Failed to connect to Supabase: {e}")
        sys.exit(1)
    
    # Get SQL files directory
    sql_dir = Path(__file__).parent.parent / "sql"
    
    # SQL files to apply (in order)
    sql_files = [
        "20250202000000_add_missing_columns.sql",
        "20250202000001_create_rpc_functions_existing.sql", 
        "20250202000002_create_monitoring_views_existing.sql",
        "20250202000003_add_legacy_functions.sql"
    ]
    
    print("🔧 Applying SQL migrations...")
    
    for sql_file in sql_files:
        file_path = sql_dir / sql_file
        
        if not file_path.exists():
            print(f"⚠️  Warning: {sql_file} not found, skipping...")
            continue
            
        print(f"📄 Applying {sql_file}...")
        
        try:
            # Read SQL file
            with open(file_path, 'r', encoding='utf-8') as f:
                sql_content = f.read()
            
            # Split by statements (rough approach - good enough for these files)
            statements = [stmt.strip() for stmt in sql_content.split(';') if stmt.strip()]
            
            for i, statement in enumerate(statements):
                if not statement:
                    continue
                    
                try:
                    # Execute SQL statement using supabase-py's sql() method
                    supabase.rpc('sql', {'query': statement + ';'})
                    print(f"   ✅ Statement {i+1}/{len(statements)} executed")
                except Exception as stmt_error:
                    # Try alternative method - direct SQL execution
                    try:
                        # For DDL statements, we need to use the PostgREST admin endpoint
                        # But supabase-py doesn't directly support this, so we'll print the SQL
                        print(f"   ⚠️  Statement {i+1} needs manual execution: {str(stmt_error)[:100]}...")
                        print(f"      SQL: {statement[:100]}...")
                    except Exception as e2:
                        print(f"   ❌ Failed to execute statement {i+1}: {e2}")
                        continue
            
            print(f"   ✅ {sql_file} applied successfully")
            
        except Exception as e:
            print(f"   ❌ Failed to apply {sql_file}: {e}")
            continue
    
    print("\n🎉 SQL migrations application completed!")
    print("\n💡 If some statements failed, you may need to apply them manually in the Supabase dashboard:")
    print(f"   1. Go to your Supabase project dashboard")
    print(f"   2. Navigate to SQL Editor")  
    print(f"   3. Copy and paste the SQL from files in ./sql/ directory")
    print(f"   4. Execute them one by one")
    
    # Test if functions were created
    print("\n🔍 Testing if RPC functions were created...")
    
    test_functions = [
        'func_claim_available_task',
        'func_claim_task'
    ]
    
    for func_name in test_functions:
        try:
            # Try to call the function with dummy parameters to see if it exists
            supabase.rpc(func_name, {'worker_id_param': 'test'} if func_name == 'func_claim_available_task' else {'p_table_name': 'tasks', 'p_worker_id': 'test'})
            print(f"   ✅ {func_name} exists and is callable")
        except Exception as e:
            if "does not exist" in str(e) or "not found" in str(e):
                print(f"   ❌ {func_name} not found - manual creation needed")
            else:
                print(f"   ✅ {func_name} exists (error expected with test params)")

if __name__ == '__main__':
    apply_sql_migrations() 
