import os

with open('README_NEW.md', 'w', encoding='utf-8') as out:
    out.write('# AskBuddy - Complete Project Documentation\n\n')
    out.write('## Table of Contents\n\n')
    out.write('- [Overview](#overview)\n')
    out.write('- [Architecture Audit](#architecture-audit)\n')
    out.write('- [Phase A Audit](#phase-a-audit)\n')
    out.write('- [Phase B-C Implementation](#phase-b-c-implementation)\n')
    out.write('- [Integration Summary](#integration-summary)\n\n')
    out.write('---\n\n')
    out.write('## Overview\n\n')
    with open('README_OLD.md', 'r', encoding='utf-8') as f:
        out.write(f.read())
    out.write('\n\n---\n\n')
    out.write('## Architecture Audit\n\n')
    with open('AUDIT_PHASE_A.md', 'r', encoding='utf-8') as f:
        out.write(f.read())
    out.write('\n\n---\n\n')
    out.write('## Phase A Audit\n\n')
    with open('PHASE_A_AUDIT.md', 'r', encoding='utf-8') as f:
        out.write(f.read())
    out.write('\n\n---\n\n')
    out.write('## Phase B-C Implementation\n\n')
    with open('PHASE_BC_SUMMARY.md', 'r', encoding='utf-8') as f:
        out.write(f.read())
    out.write('\n\n---\n\n')
    out.write('## Integration Summary\n\n')
    with open('INTEGRATION_SUMMARY.md', 'r', encoding='utf-8') as f:
        out.write(f.read())
print('done')
