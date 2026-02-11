from fastapi import FastAPI, HTTPException, UploadFile, File
from pydantic import BaseModel
from app.services.translator import translate_mssql_to_postgres
import uvicorn
import os

app = FastAPI(
    title="SQL Dialect Converter", 
    description="Converts MS SQL to PostgreSQL with AST visualization"
)

class ConversionRequest(BaseModel):
    sql: str

@app.get("/")
async def root():
    return {"message": "SQL Dialect Converter API is running"}

@app.post("/convert")
async def convert_sql(request: ConversionRequest):
    result = translate_mssql_to_postgres(request.sql)
    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["error"])
    return result

@app.post("/convert-file")
async def convert_file(file: UploadFile = File(...)):
    if not file.filename.endswith(".sql"):
        raise HTTPException(status_code=400, detail="Only .sql files are supported")
    
    content = await file.read()
    sql_code = content.decode("utf-8")
    
    result = translate_mssql_to_postgres(sql_code)
    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["error"])
    
    # Save to file
    output_dir = "output"
    os.makedirs(output_dir, exist_ok=True)
    file_path = os.path.join(output_dir, "translated_query.sql")
    
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(result["translated_sql"])
    
    result["saved_to"] = file_path
    return result

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
