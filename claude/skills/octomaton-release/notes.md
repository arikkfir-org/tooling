Octomaton $version. Images `$images/octomaton:$tag` and `$images/octomaton-logs:$tag`; `deploy/` at `$tag`.

$sections

$changelog

## Installing and upgrading

[Installing Octomaton](${install_url}) takes a new host from nothing to a first check run; an existing host moves `deploy/`'s `ref` and the images' tags to `$tag` together. Every commit on `main` stays available as before, tagged with its short SHA.
