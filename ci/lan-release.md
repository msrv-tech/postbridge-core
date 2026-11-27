# Hosted release CI

Postbridge hosted releases run in the office Gitea pipeline. Commit changes in
the local Core and SaaS `main` branches. The operator uses the publisher in
the SaaS deployment runbook to push the selected commits. The release service
starts after both Gitea references match, runs the Core and SaaS checks, and
delivers the verified images to RU and IO. The public GitHub copy retains
these sources for offsite recovery.
