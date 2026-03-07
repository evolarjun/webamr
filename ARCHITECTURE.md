# AMRFinderPlus Cloud-Native Architecture

```mermaid
flowchart TD
    User([User / Browser])
    
    subgraph Frontend [Flask Web App (Cloud Run)]
        UI[Upload UI & Param Controls]
        App[Flask App logic]
    end
    
    subgraph GCP_Storage [Google Cloud Storage]
        BucketIn[(Input Bucket\nUploads)]
        BucketOut[(Output Bucket\nResults)]
    end
    
    subgraph GCP_PubSub [Google Cloud Pub/Sub]
        Queue[[Job Queue Topic]]
    end
    
    subgraph Workers [Python Workers on Cloud Run / GCE]
        Worker[Dockerized Worker\nAMRFinderPlus]
    end
    
    subgraph GCP_DB [Cloud Firestore]
        DB[(Job Status DB)]
    end

    %% Flow steps
    User -->|Selects File & Params| UI
    UI -->|Submits Form| App
    
    App -->|1. Upload Files| BucketIn
    App -->|2. Create Job Record: Pending| DB
    App -->|3. Publish Job Msg| Queue
    App -.->|Returns Status Page| UI
    
    Queue == 4. Push Job Msg ==> Worker
    Worker -->|Update Status: Processing| DB
    Worker -.->|5. Download FASTA/Proteins| BucketIn
    
    Worker -->|6. Execute AMRFinderPlus| Worker
    
    Worker == 7. Upload TSV Result ==> BucketOut
    Worker -->|Update Status: Completed| DB
    
    UI -->|Poll /get-results| App
    App -.->|Check File/DB| BucketOut
    App -.->|Fallback Error Check| DB
    
    App -->|Render Table| User
```
