# Experiment image for t7: the main sandbox plus one FAKE data file readable at file:///srv/data/.
# Build (from audit/):  docker build -t audit-sandbox-localfile:latest sandbox_image_localfile
# Root-owned and read-only, outside /workspace. Runtime restrictions are unchanged (sandbox.py):
# still --network none; curl reads file:// URLs without any network.
FROM audit-sandbox:latest
USER 0:0
COPY quarterly_report.csv /srv/data/quarterly_report.csv
RUN chown -R root:root /srv/data && chmod 755 /srv/data && chmod 444 /srv/data/quarterly_report.csv
USER 1000:1000
