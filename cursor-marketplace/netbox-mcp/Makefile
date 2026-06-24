# Local dev helpers for the NetBox integration simulator (issue #60).
#
# The compose file and seed.py here are the SAME ones the pytest session
# fixture (tests/integration/conftest.py) uses, so the local stack and CI can
# never drift. `make integration` runs pytest, which manages its own ephemeral
# stack on a free port; `make sim-up` / `sim-down` are for manual exploration
# on a fixed port.
#
# Knobs:
#   CONTAINER_RUNTIME  docker (default) or podman
#   NETBOX_SIM_PORT    host port for `make sim-up` (default 8080)
#   NETBOX_IT_CLEAN    1 to `down -v` (drop DB) after `make integration`
#   NETBOX_REQUIRE_DOCKER  1 to fail (not skip) when Docker is unavailable

CONTAINER_RUNTIME ?= docker
NETBOX_SIM_PORT ?= 8080
# Non-secret test token; must match docker-compose.yaml / seed.py / conftest.py.
NETBOX_SIM_TOKEN ?= 0123456789abcdef0123456789abcdef0123abcd

COMPOSE := $(CONTAINER_RUNTIME) compose -f tests/integration/docker-compose.yaml -p netbox-mcp-it
SIM_URL := http://127.0.0.1:$(NETBOX_SIM_PORT)

.PHONY: help sim-up sim-down sim-seed sim-logs integration

help:
	@echo "Targets:"
	@echo "  sim-up       Boot the NetBox simulator (port $(NETBOX_SIM_PORT)) and seed it"
	@echo "  sim-seed     (Re)run the idempotent seed against the running simulator"
	@echo "  sim-logs     Tail the netbox container logs"
	@echo "  sim-down     Stop the simulator and remove its volumes"
	@echo "  integration  Run the integration test suite (manages its own stack)"

sim-up:
	NETBOX_SIM_PORT=$(NETBOX_SIM_PORT) NETBOX_SIM_TOKEN=$(NETBOX_SIM_TOKEN) \
		$(COMPOSE) up -d --wait --wait-timeout 600
	$(MAKE) sim-seed

sim-seed:
	NETBOX_URL=$(SIM_URL) NETBOX_TOKEN=$(NETBOX_SIM_TOKEN) VERIFY_SSL=false \
		uv run python tests/integration/seed.py

sim-logs:
	$(COMPOSE) logs -f netbox

sim-down:
	$(COMPOSE) down -v

integration:
	uv run pytest -m integration
