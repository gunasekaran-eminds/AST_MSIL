from fastapi import FastAPI, HTTPException, UploadFile, File
from pydantic import BaseModel
from app.services.translator import translate_mssql_to_plsql
import uvicorn

app = FastAPI(
    title="SQL Dialect Converter", 
    description="Converts MS SQL to PL/SQL (Oracle dialect) with AST visualization"
)

class ConversionRequest(BaseModel):
    sql: str

@app.get("/")
async def root():
    return {"message": "SQL Dialect Converter API is running"}

@app.post("/convert")
async def convert_sql(request: ConversionRequest):
    result = translate_mssql_to_plsql(request.sql)
    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["error"])
    return result

@app.post("/convert-file")
async def convert_file(file: UploadFile = File(...)):
    if not file.filename.endswith(".sql"):
        raise HTTPException(status_code=400, detail="Only .sql files are supported")
    
    content = await file.read()
    sql_code = content.decode("utf-8")
    
    result = translate_mssql_to_plsql(sql_code)
    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["error"])
    return result

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
