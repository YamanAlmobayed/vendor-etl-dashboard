import openpyxl
from openpyxl.styles import Font

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