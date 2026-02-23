from io import BytesIO

import qrcode


def build_pickup_qr_file(qr_code_value: str, cell_number: str | None, expires_at: str):
    qr_payload = (
        "selfstorage:pickup\n"
        f"agreement={qr_code_value}\n"
        f"cell={cell_number}\n"
        f"expires_at={expires_at}"
    )
    qr_image = qrcode.make(qr_payload)
    qr_buffer = BytesIO()
    qr_image.save(qr_buffer, format="PNG")
    qr_buffer.seek(0)
    qr_buffer.name = f"pickup_{qr_code_value}.png"
    return qr_buffer

