# Modhunter Wiki source

These files form the complete GitHub Wiki. GitHub Wiki is **a separate Git repository**, not the main repository's `/wiki` folder. The main repository also offers these pages directly.

To synchronize:

1. Open `https://github.com/Herbertofury/Modhunter/wiki` and create the first **Home** page (if not already initialized).
2. From repository **Actions**, run **Sync GitHub Wiki**. The workflow clones `Herbertofury/Modhunter.wiki.git`, copies this directory, and pushes only if anything changed.
3. If the built-in GitHub token cannot push to the Wiki, add the repository secret `MODHUNTER_WIKI_TOKEN` with repository write permission and rerun the workflow. This is a GitHub permission requirement, not an app runtime key.

You can also run `scripts/sync_wiki.sh` locally with an authenticated git credential. It will refuse to overwrite an uninitialized Wiki blindly.
