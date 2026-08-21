import unittest
from unittest.mock import MagicMock, patch, call
import os
import sys

# Mock google.cloud before anything else
sys.modules["google"] = MagicMock()
sys.modules["google.cloud"] = MagicMock()
sys.modules["google.cloud.firestore"] = MagicMock()

# Ensure the root directory is in sys.path so we can import clear_queue
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

with patch.dict(os.environ, {"PROJECT_ID": "test-project"}):
    import clear_queue

class TestClearQueue(unittest.TestCase):
    @patch("clear_queue.firestore.Client")
    @patch.dict(os.environ, {"PROJECT_ID": "test-project"})
    def test_clear_queued_jobs_calls_batch(self, mock_firestore_client):
        # Setup mocks
        mock_db = mock_firestore_client.return_value
        mock_batch = mock_db.batch.return_value

        # Mock docs
        mock_docs = [MagicMock() for _ in range(505)]
        for i, doc in enumerate(mock_docs):
            doc.id = f"job{i}"
            doc.reference = MagicMock()

        # Setup the where().stream() chain
        mock_query_queued = MagicMock()
        mock_query_queued.stream.return_value = mock_docs[:300]

        mock_query_proc = MagicMock()
        mock_query_proc.stream.return_value = mock_docs[300:]

        def where_side_effect(field, op, value):
            if value == "Queued":
                return mock_query_queued
            if value == "Processing":
                return mock_query_proc
            return MagicMock()

        mock_db.collection.return_value.where.side_effect = where_side_effect

        # Run the function
        clear_queue.clear_queued_jobs()

        # Verify batching
        assert mock_db.batch.call_count == 2
        assert mock_batch.update.call_count == 505
        assert mock_batch.commit.call_count == 2

if __name__ == "__main__":
    unittest.main()
