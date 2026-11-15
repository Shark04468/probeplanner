"""Use unchanged mesh validation with an explicit new backend identity."""
import prepare_warm_assets as preparation
from fresh_candidate_identity import contract

if __name__ == '__main__':
    c = contract()
    preparation.SOURCE_COMMIT = c['sourceCommit']
    preparation.OPTIMIZER_SHA = c['optimizerSha256']
    raise SystemExit(preparation.main())
