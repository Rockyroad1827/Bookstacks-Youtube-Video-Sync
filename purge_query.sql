

-- Show The Current Deleted items before deletion
SELECT *
FROM Database_name.deletions;


-- Permanent Deletion Query:
DELETE
FROM Database_name.deletions
WHERE deleted_by = UserID;

-- Delete Audit Log
DELETE
FROM Database_name.activities
Where user_id = UserID;

-- Delete "Deleted items link" from the recycle bin
DELETE
FROM Database_name.chapters
WHERE deleted_at IS NOT NULL
AND owned_by = UserID;

-- Delete "Deleted items link" from the recycle bin
DELETE
FROM Database_name.pages
WHERE deleted_at IS NOT NULL

AND owned_by = UserID;
