import sqlglot
import re
from sqlglot import exp, transpile, ErrorLevel

def refine_plsql(sql: str) -> str:
    """
    Refines the generated PL/SQL to fix common procedural logic errors.
    """
    if not sql:
        return sql
    
    # 1. Fix variable assignments in SELECT: SELECT @Var = Col -> SELECT Col INTO Var
    sql = re.sub(r"SELECT @(\w+)\s*=\s*(\w+)", r"SELECT \2 INTO \1", sql)
    
    # 2. Fix variable declarations: DECLARE @Var AS TYPE = VAL; -> Var TYPE := VAL;
    sql = re.sub(r"DECLARE @(\w+)(?:\s+AS)?\s+(\w+)\s*=\s*(.*?);", r"\1 \2 := \3;", sql, flags=re.IGNORECASE)
    sql = re.sub(r"DECLARE @(\w+)(?:\s+AS)?\s+(\w+);", r"\1 \2;", sql, flags=re.IGNORECASE)
    
    # 3. Fix variable assignments: SET @Var = Val; -> Var := Val;
    # (Only at the start of a line or after a semicolon/space to avoid matching UPDATE SET)
    sql = re.sub(r"(?<=[\s;])SET @(\w+)\s*=\s*(.*?);", r"\1 := \2;", sql)
    sql = re.sub(r"^SET @(\w+)\s*=\s*(.*?);", r"\1 := \2;", sql, flags=re.MULTILINE)
    
    # 4. Fix PRINT -> DBMS_OUTPUT.PUT_LINE
    sql = re.sub(r"PRINT (.*?);", r"DBMS_OUTPUT.PUT_LINE(\1);", sql)
    
    # 5. ELSE IF -> ELSIF
    sql = re.sub(r"ELSE IF", "ELSIF", sql)

    # 6. Fix CASE WHEN ... THEN  END; (with potential multiple spaces)
    sql = re.sub(r"CASE WHEN (.*?) THEN \s*END;", r"IF \1 THEN\n    NULL;\nEND IF;", sql)

    # 7. Remove @ from variables in remaining places
    sql = sql.replace("@", "")

    # 8. Final syntax cleanup for IF blocks followed by BEGIN
    sql = re.sub(r"IF (.*?) THEN\n    NULL;\nEND IF;\s*BEGIN", r"IF \1 THEN", sql)
    sql = re.sub(r"THEN\s*BEGIN", r"THEN", sql)
    
    return sql

def translate_mssql_to_plsql(sql_code: str) -> dict:
    """
    Translates MS SQL Server SQL to PL/SQL SQL.
    Shows the intermediate AST steps.
    """
    try:
        # 1. Try Structured Parsing first
        try:
            mssql_expressions = sqlglot.parse(sql_code, read="tsql")
            mssql_ast_str = "\n\n".join(repr(e) for e in mssql_expressions)

            # 2. Transform MSSQL AST to PLSQL/Oracle AST
            plsql_statements = transpile(sql_code, read="tsql", write="oracle")
            plsql_sql = ";\n".join(plsql_statements) + ";"
            
            # 3. Get the "PLSQL AST" from the generated SQL
            plsql_expressions = sqlglot.parse(plsql_sql, read="oracle")
            plsql_ast_str = "\n\n".join(repr(e) for e in plsql_expressions)
            status = "success"
        except Exception as parse_err:
            # Fallback to Best Effort Transpilation if strict parsing fails
            # This is common for complex procedural T-SQL
            plsql_statements = transpile(sql_code, read="tsql", write="oracle", error_level=ErrorLevel.IGNORE)
            plsql_sql = ";\n".join(plsql_statements) + ";"
            mssql_ast_str = f"Parsing Error (Structured): {str(parse_err)}"
            plsql_ast_str = "AST not available due to parsing errors. Showing best-effort SQL."
            status = "partial_success"

        # Apply refinement layer
        plsql_sql = refine_plsql(plsql_sql)

        return {
            "original_sql": sql_code,
            "mssql_ast": mssql_ast_str,
            "plsql_ast": plsql_ast_str,
            "translated_sql": plsql_sql,
            "status": status
        }
    except Exception as e:
        return {
            "original_sql": sql_code,
            "error": str(e),
            "status": "error"
        }
