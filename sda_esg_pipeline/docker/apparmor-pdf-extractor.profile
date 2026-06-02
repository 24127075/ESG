# AppArmor profile for the sandboxed PDF-extraction worker (SDAD §4.1).
#
# Load on the host, then run the container with:
#   sudo apparmor_parser -r -W docker/apparmor-pdf-extractor.profile
#   docker run --security-opt apparmor=esg-pdf-extractor ...
#
# Intent: an extraction worker only needs to read input PDFs, write chunk
# output, and execute the tesseract/ghostscript toolchain. It must NOT open
# raw network sockets or escalate privileges.

#include <tunables/global>

profile esg-pdf-extractor flags=(attach_disconnected,mediate_deleted) {
  #include <abstractions/base>
  #include <abstractions/python>

  # Toolchain binaries.
  /usr/bin/python3*       ix,
  /usr/bin/tesseract      ix,
  /usr/bin/gs             ix,
  /usr/bin/pdftoppm       ix,

  # Read-only access to code and input documents.
  /app/**                 r,
  /app/data/**            r,

  # Writable scratch + output only.
  /app/out/**             rw,
  /tmp/**                 rw,

  # Tesseract language data.
  /usr/share/tesseract-ocr/** r,
  /usr/share/tessdata/**      r,

  # Deny privilege escalation and raw sockets.
  deny capability sys_admin,
  deny capability setuid,
  deny capability setgid,
  deny network raw,
  deny network packet,

  # Allow loopback (Redis/Postgres reached via service DNS go through TCP;
  # tighten further with a network namespace / egress policy in production).
  network inet stream,
  network inet6 stream,
}
