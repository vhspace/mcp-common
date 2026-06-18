FROM python:3.12-slim

# Install Playwright system dependencies
RUN apt-get update && apt-get install -y \
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libx11-6 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy application code first
COPY src/ ./src/
COPY pyproject.toml .
COPY README.md .

# Install the package
RUN pip install --no-cache-dir -e .

# Install Playwright browsers
RUN playwright install chromium

# Create cache directory
RUN mkdir -p /root/.cache/vendor_portals

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Run MCP server
CMD ["python", "-m", "dc_support_mcp.mcp_server"]
