import datetime
import os
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

CERT_FILE, KEY_FILE = "cert.pem", "key.pem"

def generate_self_signed_cert():
    if os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE):
        print(f"'{CERT_FILE}' and '{KEY_FILE}' already exist. Skipping.")
        return
    print("Generating a new self-signed SSL certificate...")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, u"US"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, u"AnyState"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, u"AnyCity"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, u"Operations Dashboard"),
        x509.NameAttribute(NameOID.COMMON_NAME, u"localhost"),
    ])
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer)
            .public_key(private_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
            .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(private_key, hashes.SHA256()))
    with open(KEY_FILE, "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()))
    with open(CERT_FILE, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    print("\nCertificate and key generated successfully.")

if __name__ == "__main__":
    generate_self_signed_cert()
