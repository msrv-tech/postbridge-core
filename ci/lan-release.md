# Hosted release CI

Postbridge hosted releases run in the office Gitea pipeline. Changes to Core
main trigger the LAN release service, which pairs this exact Core revision with
SaaS main and runs Core and SaaS source gates before regional image delivery.
The public GitHub copy retains these sources for offsite recovery.
