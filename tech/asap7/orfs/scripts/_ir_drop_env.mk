# Dumps ORFS-computed env vars (RESULTS_DIR, TECH_LEF, LIB_FILES, all
# defaults.py defaults like OPENROAD_HIERARCHICAL, RECOVER_POWER, …) so
# ir_drop.sh can pass them to a standalone openroad invocation via
# `--env-file`. Include `$(FLOW_HOME)/Makefile` to inherit platform +
# design vars without re-implementing the chain.
include $(FLOW_HOME)/Makefile

# The recipe runs in a sub-shell with ORFS's exported env, so `env` is
# the source of truth — covers OPENROAD_HIERARCHICAL, RECOVER_POWER, all
# the defaults.py defaults plus the platform-level config. Filter out
# multiline / unsafe values; docker --env-file rejects them.
.PHONY: print-env
print-env:
	@echo "ORFS_ENV_BEGIN"
	@env | grep -E '^[A-Z_][A-Z0-9_]*=' \
	  | grep -v -E '^(PATH|PWD|HOME|HOSTNAME|SHLVL|TERM|MAKEFLAGS|MAKELEVEL|MAKE|_=|OLDPWD|LANG|LD_LIBRARY_PATH)='
	@echo "ORFS_ENV_END"
