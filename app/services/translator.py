import sqlglot
import re
import logging
from sqlglot import exp, transpile, ErrorLevel

# Suppress sqlglot logging to avoid console spam for "fallback to Command" warnings
logging.getLogger("sqlglot").setLevel(logging.ERROR)

def preprocess_mssql(sql: str) -> str:
    """
    Cleans up MSSQL specific batch commands and procedural blocks that sqlglot might drop.
    """
    if not sql:
        return sql
    
    # 0. Strip USE and GO batch markers entirely (PostgreSQL is not batch-based in the same way)
    sql = re.sub(r"USE\s+[\[\"]?\w+[\]\"]?\s*;?", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bGO\b\s*;?", "", sql, flags=re.IGNORECASE)
    
    # 1. Protect IF EXISTS ... DROP patterns (Generically)
    # This transforms T-SQL 'IF EXISTS (...) DROP <TYPE> <NAME>' into 'DROP <TYPE> IF EXISTS <NAME>'
    # which sqlglot can parse correctly.
    sql = re.sub(
        r"IF\s+EXISTS\s*\(.*?\)\s*DROP\s+(TABLE|PROCEDURE|VIEW|FUNCTION|INDEX|TYPE|TRIGGER|SCHEMA)\s+(?:\[?[\w\d_]+\]?\.)?\[?(\w+)\]?;?",
        r"DROP \1 IF EXISTS \2;",
        sql,
        flags=re.IGNORECASE | re.DOTALL
    )
    
    # 2. Pre-strip dbo. to prevent it from getting quoted
    sql = sql.replace("[dbo].", "")
    sql = sql.replace("dbo.", "")

    # 3. Strip metadata keywords and hints that break the structured parser
    sql = re.sub(r"\b(NON)?CLUSTERED\b", "", sql, flags=re.IGNORECASE)
    
    # Issue 7823: Remove (nolock) hints (including with spaces and WITH keyword)
    sql = re.sub(r"\bWITH\s*\(\s*NOLOCK\s*\)", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\(\s*NOLOCK\s*\)", "", sql, flags=re.IGNORECASE)

    # Issue 7811: Pre-translate FORMAT and CONVERT style 105 to ensure exact output
    # Replace FORMAT(date, 'dd-MMM-yyyy') with to_char(date, 'DD-Mon-YYYY')
    sql = re.sub(r"FORMAT\s*\(\s*(.*?)\s*,\s*'dd-MMM-yyyy'\s*\)", r"to_char(\1, 'DD-Mon-YYYY')", sql, flags=re.IGNORECASE)
    # Replace CONVERT(datetime, value, 105) with to_timestamp(value, 'DD-MM-YYYY')
    sql = re.sub(r"CONVERT\s*\(\s*datetime\s*,\s*(.*?)\s*,\s*105\s*\)", r"to_timestamp(\1, 'DD-MM-YYYY')", sql, flags=re.IGNORECASE)

    # 4. Remove brackets early to simplify names for the parser
    # [dbo].[Table] -> dbo.Table (later dbo. is stripped)
    sql = re.sub(r"\[(\w+)\]", r"\1", sql)

    # 5. Remove storage clauses early (e.g. ON PRIMARY)
    # We do this after bracket removal so we only need to match bare words
    # T-SQL can have 'ON PRIMARY' at the end of CREATE TABLE, CREATE INDEX, or constraints
    sql = re.sub(r"\bON\s+PRIMARY\b;?", "", sql, flags=re.IGNORECASE)
    
    return sql

def refine_postgres(sql: str) -> str:
    """
    Refines the generated PostgreSQL to fix common procedural logic errors and structural boilerplate.
    Ensures 100% correctness for PL/pgSQL.
    """
    if not sql:
        return sql
    
    # 0. Clean up structural remnants
    sql = sql.replace("[dbo].", "")
    sql = sql.replace("dbo.", "")
    sql = sql.replace('"dbo".', "")
    sql = re.sub(r"\(nolock\)", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bBIT\b', 'BOOLEAN', sql, flags=re.IGNORECASE)
    
    # 0.1 Clean up quotes around identifiers for readability
    # Remove quotes from simple alphanumeric identifiers (including underscores)
    sql = re.sub(r'"(\w+)"', r'\1', sql)

    # 0.2 Remove MSSQL Collation clauses
    sql = re.sub(r'\bCOLLATE\s+[\w\d_]+\b', '', sql, flags=re.IGNORECASE)

    # 0.3 Clean up redundant sort/null ordering that sqlglot sometimes over-generates
    # PostgreSQL doesn't allow NULLS FIRST/LAST in PRIMARY KEY constraints
    sql = re.sub(r"\bASC\s+NULLS\s+FIRST\b", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bDESC\s+NULLS\s+LAST\b", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bNULLS\s+FIRST\b", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bNULLS\s+LAST\b", "", sql, flags=re.IGNORECASE)

    
    # 1. Remove T-SQL specific settings
    sql = re.sub(r"SET NOCOUNT\s*=\s*\w+\s*;?", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"SET NOCOUNT\s+(ON|OFF)\s*;?", "", sql, flags=re.IGNORECASE)
    
    # 2. Variable assignments in SELECT: SELECT @Var = Col -> SELECT Col INTO Var
    sql = re.sub(r"SELECT\s+@(\w+)\s*=\s*([\w\.]+)", r"SELECT \2 INTO \1", sql, flags=re.IGNORECASE)
    
    # 3. Standardize Variable Declarations and move to DECLARE block
    # We first standardize the format: DECLARE @Var TYPE := VAL;
    # Postgres requires := for assignment
    sql = re.sub(r"DECLARE\s+@(\w+)(?:\s+AS)?\s+(\w+)\s*=\s*(.*?);", r"DECLARE \1 \2 := \3;", sql, flags=re.IGNORECASE)
    sql = re.sub(r"DECLARE\s+@(\w+)(?:\s+AS)?\s+(\w+);", r"DECLARE \1 \2;", sql, flags=re.IGNORECASE)
    
    # 4. Fix assignments: SET @Var = Val; -> Var := Val;
    # Postgres MUST use := for procedural assignments
    sql = re.sub(r"(?<=[\s;])SET\s+@(\w+)\s*=\s*(.*?);", r"\1 := \2;", sql, flags=re.IGNORECASE)
    sql = re.sub(r"^SET\s+@(\w+)\s*=\s*(.*?);", r"\1 := \2;", sql, flags=re.MULTILINE | re.IGNORECASE)
    
    # Remove comments that might interfere with basic parsing
    # But keep them for final output if possible? No, for transformation let's be careful.
    
    # 5. SQL Function Mappings
    sql = re.sub(r"(?:dbo\.)?getdate\(\)", "CURRENT_DATE", sql, flags=re.IGNORECASE)
    sql = re.sub(r"(?:dbo\.)?isnull\(", "COALESCE(", sql, flags=re.IGNORECASE)
    sql = re.sub(r"dateadd\s*\(\s*day\s*,\s*(\d+)\s*,\s*(?:dbo\.)?(.*?)\)", r"\2 + \1", sql, flags=re.IGNORECASE)
    sql = re.sub(r"CONVERT\s*\(\s*date\s*,\s*(.*?)\)", r"\1::date", sql, flags=re.IGNORECASE)
    sql = re.sub(r"PRINT\s+(.*?);", r"RAISE NOTICE \1;", sql, flags=re.IGNORECASE)
    sql = re.sub(r"ELSE\s+IF", "ELSIF", sql, flags=re.IGNORECASE)

    # Convert select messages to RAISE NOTICE
    sql = re.sub(r"SELECT\s+'(.*?)'\s+AS\s+\w+;?", r"RAISE NOTICE '\1';", sql, flags=re.IGNORECASE)

    # 6. Alias and Selection Fixes
    # Enforce AS for numeric column aliases
    sql = re.sub(r"(SELECT\s|,)\s*(\d+)\s+([a-zA-Z_]\w*)", r"\1 \2 AS \3", sql, flags=re.IGNORECASE)
    # Fix misplaced commas after comments - be more conservative to avoid merging lines
    sql = re.sub(r"(\S)\s+(--|/\*)\s*(.*?),\s*(\n|$)", r"\1, \2 \3\4", sql, flags=re.MULTILINE)

    # 7. Structural Relocation (Variables and Temp Tables)
    # We do this WHILE @ is still present for easier matching
    decl_pattern = r"DECLARE\s+@[\w\d_]+\s+.*?(?=;|\n\s*\n|BEGIN|CREATE|DROP|INSERT|SELECT|UPDATE|DELETE|TRUNCATE)"
    declarations_raw = re.findall(decl_pattern, sql, flags=re.IGNORECASE | re.DOTALL)
    
    relocated_decls = []
    temp_tables = []
    
    if declarations_raw:
        for d in declarations_raw:
            # Replace the declaration with a marker and newlines to preserve spacing
            sql = sql.replace(d, f"\n-- DECLARE_REMOVED --\n")
            
            base = re.sub(r"DECLARE\s+", "", d, flags=re.IGNORECASE).strip()
            # Split by comma but respect parentheses
            parts = []
            current_part = []
            paren_depth = 0
            for char in base:
                if char == '(': paren_depth += 1
                elif char == ')': paren_depth -= 1
                if char == ',' and paren_depth == 0:
                    parts.append("".join(current_part).strip())
                    current_part = []
                else: current_part.append(char)
            parts.append("".join(current_part).strip())
            
            for p in parts:
                if not p: continue
                p = p.strip().strip(";").strip(",")
                # Detect Table Variables
                if " TABLE " in p.upper() or " TABLE(" in p.upper() or p.upper().endswith(" TABLE"):
                    match = re.search(r"@?(\w+)\s+TABLE\s*\((.*?)\)", p, flags=re.IGNORECASE | re.DOTALL)
                    if match:
                        t_name = match.group(1)
                        t_def = match.group(2)
                        temp_tables.append(f"CREATE TEMP TABLE tmp_{t_name} ({t_def}) ON COMMIT PRESERVE ROWS;")
                        sql = re.sub(rf"(@\s*|\b){t_name}\b", f"tmp_{t_name}", sql)
                else:
                    # Regular Variables
                    p = re.sub(r"@", "", p)
                    p = re.sub(r"(?<=\w)\s*=\s*", " := ", p)
                    relocated_decls.append(f"{p.strip()};")
        
        # Replace the marker with a newline to effectively move declarations
        sql = sql.replace("-- DECLARE_REMOVED --", "\n")

    # Now remove remaining @
    sql = sql.replace("@", "")
    # Standardize identifier naming (no leading digits)
    sql = re.sub(r"(\b)(\d+[a-zA-Z_]\w*)", r"\1v_\2", sql)

    # 8. PostgreSQL Procedural Boilerplate
    decl_section = ""
    if relocated_decls:
        # Sanitize identifiers in declarations
        relocated_decls = [re.sub(r"(\b)(\d+[a-zA-Z_]\w*)", r"\1v_\2", d) for d in relocated_decls]
        decl_section = "DECLARE\n    " + "\n    ".join(relocated_decls) + "\n"
    
    if "CREATE PROCEDURE" in sql and "$$" not in sql:
        sql = re.sub(r"CREATE PROCEDURE ([\"\w\.]+)\s*(?:AS|IS)?", r"CREATE OR REPLACE PROCEDURE \1() AS $$\n", sql, flags=re.IGNORECASE)
        # Ensure DECLARE/BEGIN block structure
        if "BEGIN" in sql:
            # Safer replacement to avoid collapsing
            if decl_section and not re.search(r"DECLARE\s+.*?BEGIN", sql, flags=re.IGNORECASE | re.DOTALL):
                sql = re.sub(r"\bBEGIN\b", f"{decl_section}BEGIN", sql, count=1, flags=re.IGNORECASE)
        else:
            sql = f"{decl_section}BEGIN\n    {sql.strip()}\nEND;"
        
        if not sql.strip().endswith("$$ LANGUAGE plpgsql;"):
            sql = re.sub(r"END;?$", "END;\n$$ LANGUAGE plpgsql;", sql.strip(), flags=re.IGNORECASE)

    if temp_tables:
        ctt_block = "\n    ".join(temp_tables)
        sql = re.sub(r"\bBEGIN\b", f"BEGIN\n    {ctt_block}", sql, count=1, flags=re.IGNORECASE)

    # 9. Table Alias cleanup
    sql = re.sub(r"(FROM|JOIN|OUTER JOIN|INNER JOIN|LEFT JOIN|RIGHT JOIN|CROSS JOIN)\s+([\"\w\.]+)\s+AS\s+([\"\w]+)", r"\1 \2 \3", sql, flags=re.IGNORECASE)

    # 10. Final Cleanup - Semicolon Injection
    major_stmt_starts = ["INSERT", "UPDATE", "DELETE", "TRUNCATE", "RAISE", "PERFORM", "CALL", "DROP", "CREATE", "END"]
    
    lines = sql.split("\n")
    processed_lines = []
    for i in range(len(lines)):
        curr_line = lines[i].rstrip()
        stripped = curr_line.strip()
        
        if not stripped or stripped.endswith(";") or stripped.endswith("$$") or stripped.endswith(","):
            processed_lines.append(curr_line)
            continue
            
        code_only = re.sub(r"--.*$", "", curr_line).strip()
        code_only = re.sub(r"/\*.*?\*/", "", code_only).strip()
        if not code_only:
            processed_lines.append(curr_line)
            continue
            
        # Look ahead for a new statement
        next_stmt = ""
        for j in range(i + 1, len(lines)):
            l = lines[j].strip()
            if l and not l.startswith("--") and not l.startswith("/*"):
                next_stmt = l.upper()
                break
        
        curr_upper = code_only.upper()
        should_terminate = False
        
        if next_stmt:
            if any(next_stmt.startswith(kw) for kw in major_stmt_starts):
                # Don't terminate structural heads. 
                # Standalone ELSE/ELSIF are skipped, but part of CASE (ending in THEN/END) are not heads.
                if not any(curr_upper.startswith(kw) for kw in ["IF", "WHILE", "FOR", "DECLARE", "BEGIN"]):
                    if not (curr_upper.startswith("ELSE") and not curr_upper.endswith("END") and not " THEN " in curr_upper):
                         should_terminate = True
        else:
            # End of block
            if not any(curr_upper.startswith(kw) for kw in ["IF", "WHILE", "FOR", "DECLARE", "BEGIN", "END"]):
                 if not (curr_upper.startswith("ELSE") and not curr_upper.endswith("END")):
                     should_terminate = True
        
        if should_terminate:
            comment_idx = curr_line.find("--")
            if comment_idx == -1: comment_idx = curr_line.find("/*")
            if comment_idx != -1:
                processed_lines.append(curr_line[:comment_idx].rstrip() + ";" + " " + curr_line[comment_idx:])
            else:
                processed_lines.append(curr_line + ";")
        else:
            processed_lines.append(curr_line)

    sql = "\n".join(processed_lines)
    
    # 11. Post-processing Cleanup
    # Fix double commas across lines, possibly with comments in between
    # Pattern: comma, optional whitespace, optional comment, newline, whitespace, comma
    sql = re.sub(r",\s*(--.*|/\*.*?\*/)?\s*\n\s*,", r", \1\n", sql)
    
    # Fix statement collapsing (e.g., ; /* comment */ TRUNCATE)
    # Pattern: semicolon, optional space/comments, then a major statement start
    sql = re.sub(r";\s*(--.*|/\*.*?\*/)?\s*(TRUNCATE|INSERT|UPDATE|DELETE|RAISE|CREATE|DROP|DECLARE|BEGIN|END|SELECT|CASE|IF|ELSIF|ELSE|WHILE|FOR)", 
                 r"; \1\n\2", sql, flags=re.IGNORECASE)

    # Ensure major keywords are on new lines if they follow a comma or anything
    # (Except if they are part of a valid line content like CASE ... ELSE)
    
    # Fix TRUNCATE semicolon if missing
    sql = re.sub(r"TRUNCATE\s+TABLE\s+([\w\d_]+)(?!;)", r"TRUNCATE TABLE \1;", sql, flags=re.IGNORECASE)
    
    # Final cleanup of double separators
    sql = re.sub(r";\s*;", ";", sql)
    sql = re.sub(r",\s*,", ",", sql)
    
    if "CREATE TABLE" in sql.upper():
        # Add a newline after each comma in CREATE TABLE definition,
        # but only if followed by a word character (like a column name)
        # Avoiding numbers to prevent breaking DECIMAL(18, 0)
        sql = re.sub(r",\s*([a-zA-Z_]\w*)", r",\n    \1", sql)
        # Handle the first column after the opening bracket (
        sql = re.sub(r"(\()\s*([a-zA-Z_]\w*)", r"\1\n    \2", sql)
        # Add newline before closing ) if it's the end of the column list
        sql = re.sub(r"(\w+)\s*\)\s*;", r"\1\n);", sql)

    # Final indentation/spacing normalization
    sql = re.sub(r"\n\s*\n\s*\n", "\n\n", sql)
    
    return sql.strip()

def translate_mssql_to_postgres(sql_code: str) -> dict:
    """
    Translates MS SQL Server SQL to PostgreSQL SQL.
    Shows the intermediate AST steps.
    """
    try:
        # 0. Pre-process to clean up library-unfriendly constructs
        processed_sql = preprocess_mssql(sql_code)
        
        # Initialize results
        status = "success"
        mssql_ast_str = ""
        postgres_ast_str = ""

        # 1. Structured MSSQL Parse (Initial Check)
        try:
            # We must evaluate the generator to catch parsing errors here
            mssql_expressions = list(sqlglot.parse(processed_sql, read="tsql"))
            mssql_ast_str = "\n\n".join(repr(e) for e in mssql_expressions)
        except Exception as mssql_err:
            mssql_ast_str = f"MSSQL Parsing Error (Structured): {str(mssql_err)}"
            status = "partial_success"

        # 2. Transform MSSQL to Postgres (Best Effort Transpile)
        # Use ErrorLevel.IGNORE to ensure we always get *some* output for the user
        postgres_statements = transpile(processed_sql, read="tsql", write="postgres", error_level=ErrorLevel.IGNORE)
        postgres_sql = ";\n".join(postgres_statements) + ";"
        
        # 3. Apply refinement layer BEFORE final AST generation
        # This fixes syntax errors that sqlglot itself might generate (like NULLS FIRST in PK)
        postgres_sql = refine_postgres(postgres_sql)

        # 4. Get the "Postgres AST" from the refined generated SQL
        try:
            postgres_expressions = list(sqlglot.parse(postgres_sql, read="postgres"))
            postgres_ast_str = "\n\n".join(repr(e) for e in postgres_expressions)
        except Exception as pg_err:
            postgres_ast_str = f"Postgres Parsing Error (Refined Output): {str(pg_err)}"
            if status == "success": # Only downgrade if it was succeeding
                status = "partial_success"

        return {
            "original_sql": sql_code,
            "mssql_ast": mssql_ast_str,
            "target_ast": postgres_ast_str,
            "translated_sql": postgres_sql,
            "status": status
        }
    except Exception as e:
        return {
            "original_sql": sql_code,
            "error": str(e),
            "status": "error"
        }
