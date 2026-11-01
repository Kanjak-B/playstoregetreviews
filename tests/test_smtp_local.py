"""Local SMTP TLS integration: no external server or recipient."""
from email import policy
from email.parser import BytesParser
from email.message import EmailMessage
from pathlib import Path
import socketserver
import ssl
import subprocess
import threading
from playstoregetreviews.config import EmailConfig
from playstoregetreviews.notifications.email import SMTPTransport


def test_actual_smtp_over_verified_tls(tmp_path, monkeypatch):
    cert, key = tmp_path/'certificate.pem', tmp_path/'key.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
        '-keyout', str(key), '-out', str(cert), '-days', '1', '-subj', '/CN=localhost',
        '-addext', 'subjectAltName=DNS:localhost'], check=True, capture_output=True)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert, key)
    client_context = ssl.create_default_context(cafile=str(cert))
    assert client_context.check_hostname and client_context.verify_mode == ssl.CERT_REQUIRED
    messages = []

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.wfile.write(b'220 localhost test SMTP\r\n')
            while True:
                line = self.rfile.readline(8192)
                if not line:
                    break
                command = line.split(b' ', 1)[0].strip().upper()
                if command == b'EHLO':
                    self.wfile.write(b'250-localhost\r\n250 SIZE 1000000\r\n')
                elif command in {b'MAIL', b'RCPT', b'RSET'}:
                    self.wfile.write(b'250 OK\r\n')
                elif command == b'DATA':
                    self.wfile.write(b'354 End with dot\r\n')
                    chunks = []
                    while True:
                        part = self.rfile.readline(8192)
                        if part == b'.\r\n':
                            break
                        if not part:
                            return
                        chunks.append(part[1:] if part.startswith(b'..') else part)
                    messages.append(b''.join(chunks))
                    self.wfile.write(b'250 Accepted\r\n')
                elif command == b'QUIT':
                    self.wfile.write(b'221 Bye\r\n')
                    break
                else:
                    self.wfile.write(b'502 Unknown command\r\n')

    class Server(socketserver.TCPServer):
        def get_request(self):
            sock, address = super().get_request()
            sock.settimeout(5)
            return server_context.wrap_socket(sock, server_side=True), address

    monkeypatch.setattr('playstoregetreviews.notifications.email.ssl.create_default_context', lambda: client_context)
    monkeypatch.delenv('PLAYSTORE_SMTP_USERNAME', raising=False)
    monkeypatch.delenv('PLAYSTORE_SMTP_PASSWORD', raising=False)
    server = Server(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    try:
        message = EmailMessage()
        message['From'] = 'reports@example.com'
        message['To'] = 'synthetic@example.com'
        message['Subject'] = 'Rapport synthétique local'
        message.set_content('Test TLS local sans destinataire externe.')
        config = EmailConfig(enabled=True, host='localhost', port=server.server_address[1],
                             security='ssl', from_address='reports@example.com', auth_required=False)
        SMTPTransport(config).send(message)
        assert len(messages) == 1
        parsed = BytesParser(policy=policy.default).parsebytes(messages[0])
        assert parsed['To'] == 'synthetic@example.com'
        assert 'Test TLS local' in parsed.get_content()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
