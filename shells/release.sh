#!/bin/bash
# Partner Release Script
# Usage: ./release.sh v0.7.0 "Event-driven Partner runtime"

set -e

VERSION=$1
MESSAGE=$2

if [ -z "$VERSION" ] || [ -z "$MESSAGE" ]; then
    echo "Usage: ./release.sh <version> <message>"
    echo "Example: ./release.sh v0.7.0 'Event-driven Partner runtime'"
    exit 1
fi

cd "$(dirname "$0")"

echo "📦 Releasing $VERSION..."
echo "   Message: $MESSAGE"
echo ""

# Check if tag exists
if git tag -l "$VERSION" | grep -q "$VERSION"; then
    echo "⚠️  Tag $VERSION already exists!"
    read -p "Overwrite? (y/N): " confirm
    if [ "$confirm" != "y" ]; then
        echo "Cancelled."
        exit 1
    fi
    git tag -d "$VERSION"
    git push origin ":refs/tags/$VERSION" 2>/dev/null || true
fi

# Commit if there are changes
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "📝 Committing changes..."
    git add -A
    git commit -m "$VERSION: $MESSAGE"
fi

# Create tag
echo "🏷️  Creating tag $VERSION..."
git tag -a "$VERSION" -m "$VERSION: $MESSAGE"

# Push
echo "🚀 Pushing to GitHub..."
git push origin main --tags

echo ""
echo "✅ Released $VERSION!"
echo "   View: https://github.com/zty522/partner/releases/tag/$VERSION"
