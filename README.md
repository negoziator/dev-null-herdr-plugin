# dev-null-herdr-plugin

Commit to [/dev/null](https://dev-null.sh), the social network for developers, without leaving
[herdr](https://herdr.dev).

- **Composer**: a popup where you write a commit in your own editor, review it, and commit it.
- **Shipped-it drafts**: when an agent finishes a turn in which it made git commits, the plugin
  drafts a /dev/null commit about them and tells you. You review it in the composer. **Nothing is
  ever committed without you.**

Commits are signed with an SSH key that is on your GitHub account, exactly as with
`curl -fsSL https://dev-null.sh/commit | bash`. There is no login and no token.

## Requirements

- herdr 0.7.4 or later, on Linux or macOS
- Python 3.9 or later (standard library only, nothing to install)
- `bash`, `curl` and OpenSSH 8.1 or later (`ssh-keygen -Y sign`)
- an Ed25519 or RSA key, in your SSH agent or `~/.ssh`, that is on your GitHub account

## Install

```bash
herdr plugin install negoziator/dev-null-herdr-plugin
```

herdr shows what the plugin runs before you approve it.

### Keybindings

herdr doesn't bind plugin actions by itself. Add them to `~/.config/herdr/config.toml` (neither
key is a herdr default):

```toml
[[keys.command]]
key = "prefix+m"
type = "plugin_action"
command = "negoziator.dev-null.compose"
description = "commit to /dev/null"

[[keys.command]]
key = "prefix+shift+m"
type = "plugin_action"
command = "negoziator.dev-null.review-draft"
description = "review /dev/null draft"
```

Then run `herdr server reload-config`. You can also run the actions from herdr's command palette
or with `herdr plugin action invoke negoziator.dev-null.compose`.

## The composer

The composer opens `$VISUAL`, `$EDITOR`, or else `nano`, `vim` or `vi`, with help text under a
scissors line, as `git commit --verbose` does. Everything above the line is your commit, so a line
starting with `#` stays in it. Save and quit, check the preview and the character count, then
press `c` to commit, `e` to edit again, `v` to see a long commit in full, or `q` to quit. Quitting
the editor with an error (vim's `:cq`) aborts. A GUI editor has to wait for you: `code --wait`.

Control characters, such as terminal escape sequences, are removed before the preview, so what
you see is exactly what is committed.

After a commit, herdr shows its link in a notification. Commits can be pending review: /dev/null
says so in its answer.

## Shipped-it drafts

The plugin listens to herdr's `pane.agent_status_changed` event:

1. When an agent pane starts **working**, it notes the `HEAD` of the git repository the pane is in.
2. When the agent is **done**, the commits made since then become a draft for that repository:

   ```
   Shipped in dev-null-herdr-plugin:
   - Add the composer
   - Draft a commit when an agent ships

   9 files changed, 812 insertions(+)

   https://github.com/negoziator/dev-null-herdr-plugin
   ```

3. herdr tells you a draft is ready. Run **Review /dev/null draft** to edit and commit it, or
   discard it. A draft that is waiting grows with the next turn's commits.

Only commits made on the pane's own branch count: a turn drafts nothing when it made no commit,
switched branch or rewrote history (rebase, amend), and what a pull or merge brought in is left
out. A turn not seen to end within 12 hours is dropped. Commit subjects can say more than you want to share,
especially from a private repository: you see every draft before it goes anywhere.

Turn drafting off or on with the **Toggle /dev/null drafts** action, or `bin/devnull drafts off`.

## The commit script

The plugin commits with /dev/null's own script, never piped from the network. It ships a reviewed
copy in [`vendor/commit.sh`](vendor/) and compares it with `https://dev-null.sh/commit` before each
commit. When they differ, it asks:

```
New version of /dev/null available. 1) See diff 2) Update 3) Discard
```

**Update** keeps the new version in the plugin's state directory and commits with it. Read the diff
first: this is the step where you choose to trust the new script. **Discard** commits with the
current one and asks again next time. `bin/devnull check-script` runs the same check on demand,
and `bin/devnull check-script --reset` goes back to the vendored copy. A plugin release with a newer
reviewed copy replaces an update you accepted earlier.

## From a shell

`bin/devnull` works outside herdr too, and shares the drafts:

```bash
bin/devnull compose                        # the composer, in this terminal
bin/devnull compose --draft                # the newest draft
bin/devnull post 'It was DNS.'             # no editor
bin/devnull post --reply 0be0130 'Agreed!' --image shipped.png
bin/devnull post --dry-run 'Hello world!'  # shows what would run, sends nothing
bin/devnull drafts                         # lists drafts
```

## Files

| What | Where |
|---|---|
| Drafts, turn tracking, an updated commit script (all private to you, `0600`) | `$HERDR_PLUGIN_STATE_DIR` (`~/.local/state/dev-null-herdr` from a shell) |
| Settings (`config.json`) | `$HERDR_PLUGIN_CONFIG_DIR` (`~/.config/dev-null-herdr` from a shell) |

Logs: `herdr plugin log list --plugin negoziator.dev-null`.

## Development

```bash
herdr plugin link "$PWD"                         # use this checkout
python3 -m unittest discover -s tests -t . -b    # tests: no network, nothing committed
```

Script changes apply on the next run. After changing `herdr-plugin.toml`, run `herdr plugin link`
again.

## Roadmap

- A feed pane (following, latest, trending) and reply notifications. Both need /dev/null's device
  sign-in, waiting on [nunomaduro/dev-null.sh#585](https://github.com/nunomaduro/dev-null.sh/pull/585).

## Licence

MIT. `vendor/commit.sh` is /dev/null's script, under its own MIT licence: see [vendor/](vendor/).
