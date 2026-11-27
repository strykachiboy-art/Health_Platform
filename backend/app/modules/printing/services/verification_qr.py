"""Pure QR matrix used by both page planner and native wire validation."""
import qrcode
from .print_policy import PrintDenied


def rows_for(url):
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=1, border=4)
    code.add_data(url)
    code.make(fit=True)
    matrix=code.get_matrix()
    if not 29<=len(matrix)<=65 or any(len(row)!=len(matrix) for row in matrix):
        raise PrintDenied('Verification QR exceeds frame')
    return tuple(''.join('1' if cell else '0' for cell in row) for row in matrix)
