"""Code chunking for semantic indexing.

Matches Roo Code's chunking strategy:
- Tree-sitter AST for supported languages
- Line-based fallback for unsupported types
- 50-1000 char limits with 15% tolerance
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Chunking constants (from Roo Code)
MAX_BLOCK_CHARS = 1000
MIN_BLOCK_CHARS = 50
MIN_CHUNK_REMAINDER = 200
TOLERANCE_FACTOR = 1.15  # 15% tolerance on max

# File size limit
MAX_FILE_SIZE = 1_000_000  # 1MB

# Supported extensions for Tree-sitter parsing
TREESITTER_EXTENSIONS = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
}

# All supported extensions for indexing
SUPPORTED_EXTENSIONS = {
    # Python
    ".py", ".pyi",
    # JavaScript/TypeScript
    ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx",
    # Go
    ".go",
    # Rust
    ".rs",
    # Java/Kotlin/Scala
    ".java", ".kt", ".kts", ".scala",
    # C/C++
    ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hxx",
    # Ruby
    ".rb", ".rake",
    # PHP
    ".php",
    # Swift
    ".swift",
    # Markdown
    ".md", ".markdown",
    # Shell
    ".sh", ".bash", ".zsh",
    # Config/Data
    ".json", ".yaml", ".yml", ".toml",
}

# Directories to ignore (from Roo Code's isPathInIgnoredDirectory)
IGNORED_DIRECTORIES = {
    "node_modules",
    ".git",
    "dist",
    "build",
    "__pycache__",
    ".venv",
    "venv",
    ".tox",
    ".next",
    ".nuxt",
    "coverage",
    ".cache",
    ".eggs",
    "*.egg-info",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "target",  # Rust
    "vendor",  # Go
    "Pods",  # iOS
}


@dataclass
class Chunk:
    """A code chunk with metadata.

    Uses camelCase for payload compatibility with Roo Code.
    """

    filePath: str
    codeChunk: str
    startLine: int
    endLine: int
    segmentHash: str
    fileHash: str

    def to_payload(self) -> dict:
        """Convert to Qdrant payload format."""
        return {
            "filePath": self.filePath,
            "codeChunk": self.codeChunk,
            "startLine": self.startLine,
            "endLine": self.endLine,
            "segmentHash": self.segmentHash,
            "fileHash": self.fileHash,
        }


def compute_segment_hash(content: str) -> str:
    """Compute SHA256 hash of segment content."""
    return hashlib.sha256(content.encode()).hexdigest()


def is_path_ignored(path: Path) -> bool:
    """Check if path should be ignored."""
    parts = path.parts
    for part in parts:
        if part in IGNORED_DIRECTORIES:
            return True
        # Handle glob patterns like *.egg-info
        for pattern in IGNORED_DIRECTORIES:
            if "*" in pattern:
                import fnmatch
                if fnmatch.fnmatch(part, pattern):
                    return True
    return False


def should_index_file(path: Path) -> bool:
    """Check if file should be indexed."""
    if is_path_ignored(path):
        return False
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        return False
    try:
        if path.stat().st_size > MAX_FILE_SIZE:
            return False
    except OSError:
        return False
    return True


class Chunker:
    """Chunks code files into semantic segments."""

    def __init__(self):
        self._treesitter_available = False
        self._parsers: dict = {}
        self._try_init_treesitter()

    def _try_init_treesitter(self) -> None:
        """Try to initialize Tree-sitter parsers."""
        try:
            import tree_sitter_python
            import tree_sitter_javascript

            self._treesitter_available = True
            self._ts_languages = {
                "python": tree_sitter_python.language(),
                "javascript": tree_sitter_javascript.language(),
            }
            # TypeScript uses JavaScript parser as fallback
            self._ts_languages["typescript"] = tree_sitter_javascript.language()
            self._ts_languages["tsx"] = tree_sitter_javascript.language()
        except ImportError:
            self._treesitter_available = False
            self._ts_languages = {}

    def _get_parser(self, language: str):
        """Get or create Tree-sitter parser for language."""
        if not self._treesitter_available:
            return None

        if language not in self._parsers:
            try:
                import tree_sitter

                lang = self._ts_languages.get(language)
                if lang:
                    parser = tree_sitter.Parser(lang)
                    self._parsers[language] = parser
            except Exception:
                return None

        return self._parsers.get(language)

    def chunk_file(self, file_path: Path, file_hash: str) -> list[Chunk]:
        """Chunk a file into semantic segments.

        Args:
            file_path: Path to the file
            file_hash: Pre-computed file hash

        Returns:
            List of Chunk objects
        """
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []

        if not content.strip():
            return []

        # Determine chunking strategy
        ext = file_path.suffix.lower()
        language = TREESITTER_EXTENSIONS.get(ext)

        if language and self._treesitter_available:
            chunks = self._chunk_with_treesitter(content, language, file_path, file_hash)
            if chunks:
                return chunks

        # Fallback to line-based chunking
        return self._chunk_by_lines(content, file_path, file_hash)

    def _chunk_with_treesitter(
        self, content: str, language: str, file_path: Path, file_hash: str
    ) -> list[Chunk]:
        """Chunk using Tree-sitter AST."""
        parser = self._get_parser(language)
        if not parser:
            return []

        try:
            tree = parser.parse(content.encode())
            root = tree.root_node

            # Find semantic boundaries (functions, classes, etc.)
            boundaries = self._find_semantic_boundaries(root, content)

            if not boundaries:
                return []

            return self._create_chunks_from_boundaries(
                content, boundaries, file_path, file_hash
            )
        except Exception:
            return []

    def _find_semantic_boundaries(self, node, content: str) -> list[tuple[int, int]]:
        """Find semantic boundaries in AST.

        Returns list of (start_byte, end_byte) tuples for semantic blocks.
        """
        boundaries = []

        # Node types that represent semantic boundaries
        semantic_types = {
            "function_definition",
            "async_function_definition",
            "class_definition",
            "function_declaration",
            "class_declaration",
            "method_definition",
            "arrow_function",
            "export_statement",
            "import_statement",
            "import_from_statement",
        }

        def walk(node):
            if node.type in semantic_types:
                # Check size constraints
                text = content[node.start_byte : node.end_byte]
                if MIN_BLOCK_CHARS <= len(text) <= MAX_BLOCK_CHARS * TOLERANCE_FACTOR:
                    boundaries.append((node.start_byte, node.end_byte))
                elif len(text) > MAX_BLOCK_CHARS * TOLERANCE_FACTOR:
                    # Too large, walk children to find smaller boundaries
                    for child in node.children:
                        walk(child)
                # Skip if too small, will be merged with adjacent chunks
            else:
                for child in node.children:
                    walk(child)

        walk(node)

        # Sort by position
        boundaries.sort(key=lambda x: x[0])

        # Fill gaps between boundaries
        filled = []
        last_end = 0

        for start, end in boundaries:
            if start > last_end:
                # There's a gap - add it as a chunk if substantial
                gap_text = content[last_end:start].strip()
                if len(gap_text) >= MIN_BLOCK_CHARS:
                    filled.append((last_end, start))
            filled.append((start, end))
            last_end = end

        # Add trailing content
        if last_end < len(content):
            trailing = content[last_end:].strip()
            if len(trailing) >= MIN_BLOCK_CHARS:
                filled.append((last_end, len(content)))

        return filled

    def _create_chunks_from_boundaries(
        self,
        content: str,
        boundaries: list[tuple[int, int]],
        file_path: Path,
        file_hash: str,
    ) -> list[Chunk]:
        """Create Chunk objects from byte boundaries."""
        chunks = []
        lines = content.split("\n")

        for start_byte, end_byte in boundaries:
            chunk_text = content[start_byte:end_byte]

            # Skip if too small
            if len(chunk_text.strip()) < MIN_BLOCK_CHARS:
                continue

            # Split large chunks
            if len(chunk_text) > MAX_BLOCK_CHARS * TOLERANCE_FACTOR:
                sub_chunks = self._split_large_chunk(chunk_text)
            else:
                sub_chunks = [chunk_text]

            # Calculate line numbers
            start_line = content[:start_byte].count("\n") + 1

            current_line = start_line
            for sub_chunk in sub_chunks:
                sub_chunk_stripped = sub_chunk.strip()
                if len(sub_chunk_stripped) < MIN_BLOCK_CHARS:
                    continue

                end_line = current_line + sub_chunk.count("\n")

                chunks.append(
                    Chunk(
                        filePath=str(file_path),
                        codeChunk=sub_chunk_stripped,
                        startLine=current_line,
                        endLine=end_line,
                        segmentHash=compute_segment_hash(sub_chunk_stripped),
                        fileHash=file_hash,
                    )
                )

                current_line = end_line + 1

        return chunks

    def _split_large_chunk(self, text: str) -> list[str]:
        """Split a large chunk into smaller pieces."""
        chunks = []
        lines = text.split("\n")
        current_chunk_lines = []
        current_size = 0

        for line in lines:
            line_size = len(line) + 1  # +1 for newline

            if current_size + line_size > MAX_BLOCK_CHARS:
                if current_chunk_lines:
                    chunks.append("\n".join(current_chunk_lines))
                current_chunk_lines = [line]
                current_size = line_size
            else:
                current_chunk_lines.append(line)
                current_size += line_size

        if current_chunk_lines:
            chunk_text = "\n".join(current_chunk_lines)
            # Check if remainder is too small to be its own chunk
            if len(chunk_text.strip()) >= MIN_CHUNK_REMAINDER:
                chunks.append(chunk_text)
            elif chunks:
                # Merge with previous chunk if possible
                chunks[-1] = chunks[-1] + "\n" + chunk_text

        return chunks

    def _chunk_by_lines(
        self, content: str, file_path: Path, file_hash: str
    ) -> list[Chunk]:
        """Fallback line-based chunking."""
        chunks = []
        lines = content.split("\n")

        current_chunk_lines = []
        current_size = 0
        start_line = 1

        for i, line in enumerate(lines, 1):
            line_size = len(line) + 1

            if current_size + line_size > MAX_BLOCK_CHARS and current_chunk_lines:
                chunk_text = "\n".join(current_chunk_lines).strip()

                if len(chunk_text) >= MIN_BLOCK_CHARS:
                    chunks.append(
                        Chunk(
                            filePath=str(file_path),
                            codeChunk=chunk_text,
                            startLine=start_line,
                            endLine=i - 1,
                            segmentHash=compute_segment_hash(chunk_text),
                            fileHash=file_hash,
                        )
                    )

                current_chunk_lines = [line]
                current_size = line_size
                start_line = i
            else:
                current_chunk_lines.append(line)
                current_size += line_size

        # Handle remaining lines
        if current_chunk_lines:
            chunk_text = "\n".join(current_chunk_lines).strip()
            if len(chunk_text) >= MIN_BLOCK_CHARS:
                chunks.append(
                    Chunk(
                        filePath=str(file_path),
                        codeChunk=chunk_text,
                        startLine=start_line,
                        endLine=len(lines),
                        segmentHash=compute_segment_hash(chunk_text),
                        fileHash=file_hash,
                    )
                )
            elif chunks:
                # Merge with previous if too small
                prev = chunks[-1]
                merged_text = prev.codeChunk + "\n" + chunk_text
                chunks[-1] = Chunk(
                    filePath=prev.filePath,
                    codeChunk=merged_text,
                    startLine=prev.startLine,
                    endLine=len(lines),
                    segmentHash=compute_segment_hash(merged_text),
                    fileHash=file_hash,
                )

        return chunks
