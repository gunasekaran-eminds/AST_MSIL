# SQL Dialect Converter (MS SQL to PL/SQL)

A FastAPI-based tool to convert MS SQL Server scripts to PL/SQL (Oracle) using AST transformation.

## Project Structure
- `app/`: Main application package.
  - `main.py`: FastAPI entry point.
  - `services/`: Business logic.
    - `translator.py`: SQL transformation logic.
- `tests/`: Test scripts and verification tools.
- `requirements.txt`: Python dependencies.

## Setup
1. Install dependencies: `pip install -r requirements.txt`
2. Run the server: `python -m app.main`
3. Access API docs at: `http://localhost:8000/docs`

---

### 📖 Full Guide
For detailed instructions, API examples (cURL, Python), and features, check out the **[Usage Guide](USAGE_GUIDE.md)**.

