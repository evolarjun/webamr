# Observations about web app

## first attempt
Running a single Docker container with a VM running both the web browser and AMRFinderPlus seems to work fine, but I don't know how to get it to work well under load

## second attempt
Using a trigger for cloud pub/sub based on adding a file to a cloud storage bucket worked (curently not working, should have tagged the revision that worked)

## Issues 

1. Cloud pub/sub will trigger another run if the run takes too much time to respond, and response is only completed when the job completes so for longer submissions the job would be run twice.
2. Attempts to get this working by making the second trigger check for timeout and return quickly if another job has already been started have failed so far. I should get this working

## Idea for third attempt
Give up on entirely serverless and just run a small VM that spawns Cloud Run jobs based on a queue. Two options for queue handling. A small VM should only cost about $5 per month, and maybe that's worth it to make for a simpler system.
1. Use files in cloud storage buckets to manage the queue (equivalent to using files on disk that I've done before)
2. Use the firestore database in datastore mode to handle the queue. 
    - Create a database table (e.g., jobs) with columns like id, job_type, payload, status (e.g., pending, processing, completed, failed), created_at, started_at, finished_at.
    - When a job is submitted, insert a new row into the jobs table with status set to pending.
    - A worker process periodically queries the table for pending jobs, updates the status to processing, and executes the job.
    - Upon completion or failure, the worker updates the status accordingly.

Will need to figure out how to read the queue, either use a cron job or a second daemon to handle the queue.
One idea is to run two flask apps on the server. One is the front-end, the second is the queue manager. Use the front end to send a web request to trigger it when a new job is submitted.  Also have a cron job to wake it periodically to check the queue as a backup.