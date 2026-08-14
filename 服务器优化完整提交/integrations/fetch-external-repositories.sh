#!/usr/bin/env bash
set -euo pipefail

archive_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
third_party_root="${archive_root}/third_party"
mkdir -p "${third_party_root}"

fetch_repo() {
  local name="$1"
  local url="$2"
  local commit="$3"
  local target="${third_party_root}/${name}"

  if [[ ! -d "${target}/.git" ]]; then
    git clone --filter=blob:none --no-checkout "${url}" "${target}"
  fi
  git -C "${target}" fetch --depth 1 origin "${commit}"
  git -C "${target}" checkout --detach "${commit}"
}

fetch_repo "MediaCrawler" "https://github.com/NanmiCoder/MediaCrawler.git" "17f66121e0fcc40fc23958b995bec873d422667d"
fetch_repo "Scrapling" "https://github.com/D4Vinci/Scrapling.git" "80d78cc362cfe6bb7c1dc17f11a23d28a6c0208c"
fetch_repo "pytrends" "https://github.com/GeneralMills/pytrends.git" "a9984ffdc9b31d853dde2ab614a77ecbf2bf33a1"
fetch_repo "skills" "https://github.com/mattpocock/skills.git" "2ab958093e83e0ec752e6c1c5932da465bf23e0c"
fetch_repo "PaddleOCR" "https://github.com/PaddlePaddle/PaddleOCR.git" "2661c7c0ef5c613e8f93c6e93b2e052399f0f854"
fetch_repo "pyvideotrans" "https://github.com/jianchang512/pyvideotrans.git" "a21122786d3a1efda4c92b15cbeb28009d4a7e19"
fetch_repo "remotion" "https://github.com/remotion-dev/remotion.git" "4e459b8b3aeec12ac8346666773ea28892a30e31"

echo "External repositories are available under ${third_party_root}"
