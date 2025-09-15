import openpyxl
from openpyxl.styles import Font
import pandas as pd
import os

def append_to_excel(excel_path, **kwargs):
        try:
            workbook = openpyxl.load_workbook(excel_path)
            sheet = workbook.active
        except FileNotFoundError:
            workbook = openpyxl.Workbook()
            sheet = workbook.active
            bold_font = Font(bold=True)
            sheet.append(list(kwargs.get('excel_attributes').keys()))
            for cell in sheet[1]:
                cell.font = bold_font
        sheet.append(list(kwargs.get('excel_attributes').values()))
        workbook.save(excel_path)
        
def merge_excels(input_folder, output_file):
    all_data = pd.DataFrame()

    for file in os.listdir(input_folder):
        if file.endswith(".xlsx") or file.endswith(".xls"):
            file_path = os.path.join(input_folder, file)
            df = pd.read_excel(file_path)
            all_data = pd.concat([all_data, df], ignore_index=True)

    all_data.to_excel(output_file, index=False)
    print(f"Merged Excel files saved to {output_file}")        