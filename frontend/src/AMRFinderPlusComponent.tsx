import React, { useState, useCallback, useRef } from 'react';

interface JobParams {
  plus_flag: boolean;
  organism: string;
  ident_min?: number;
  coverage_min?: number;
}

export default function AMRFinderPlusComponent() {
  const [file, setFile] = useState<File | null>(null);
  const [params, setParams] = useState<JobParams>({ plus_flag: false, organism: '' });
  const [isAdvancedOpen, setIsAdvancedOpen] = useState(false);
  const [jobStatus, setJobStatus] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [tsvData, setTsvData] = useState<string | null>(null);
  
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Suggested organisms for the autocomplete dropdown
  const organisms = ['Campylobacter', 'Escherichia', 'Salmonella', 'Klebsiella', 'Acinetobacter', 'Pseudomonas'];

  const validateFasta = (f: File): boolean => {
    // Basic client-side validation
    const validExtensions = ['.fasta', '.fa', '.faa', '.fna'];
    const lowerName = f.name.toLowerCase();
    
    if (f.size === 0) {
      alert("File is empty.");
      return false;
    }
    const hasValidExt = validExtensions.some(ext => lowerName.endsWith(ext));
    if (!hasValidExt) {
      alert("Please upload a valid FASTA file (.fasta, .fa, .faa, .fna).");
      return false;
    }
    return true;
  };

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    const droppedFile = e.dataTransfer.files[0];
    if (droppedFile && validateFasta(droppedFile)) {
      setFile(droppedFile);
    }
  }, []);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      const selectedFile = e.target.files[0];
      if (validateFasta(selectedFile)) {
        setFile(selectedFile);
      }
    }
  };

  const uploadAndSubmit = async () => {
    if (!file) return;
    try {
      setJobStatus("Requesting Signed Upload URL...");
      
      // 1. Get Signed URL from Backend
      const urlRes = await fetch('http://localhost:8000/api/upload-url', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: file.name })
      });
      const urlData = await urlRes.json();
      
      setJobStatus("Uploading DIRECTLY to Cloud Storage...");
      
      // 2. Upload file directly to GCS via the Signed URL
      await fetch(urlData.signed_url, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/octet-stream' },
        body: file
      });
      
      setJobStatus("Submitting Job ID & Parameters to Queue...");
      
      // 3. Submit job to backend and trigger processing queue
      const submitRes = await fetch('http://localhost:8000/api/submit-job', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          gcs_uri: urlData.gcs_uri,
          ...params
        })
      });
      
      const submitData = await submitRes.json();
      setJobId(submitData.job_id);
      setJobStatus("Job Submitted. Processing...");
      setTsvData(null); // Clear previous runs
      
      // Start polling for status
      pollStatus(submitData.job_id);
      
    } catch (err) {
      console.error(err);
      setJobStatus(`Error: ${(err as Error).message}`);
    }
  };

  const pollStatus = async (id: string) => {
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`http://localhost:8000/api/status/${id}`);
        const data = await res.json();
        setJobStatus(`Status: ${data.status}`);
        
        if (data.status === 'Completed') {
          clearInterval(interval);
          setJobStatus(`Status: Completed! Parsing results...`);
          
          /* In production, the client would request a Signed URL for the result TSV and fetch it:
             const urlRes = await fetch(`/api/download-url/${id}`);
             const signedResultUrl = await urlRes.json();
             const resultTxt = await fetch(signedResultUrl).then(r => r.text());
             setTsvData(resultTxt); 
          */
          
          // Simulated mocked TSV Output text structure
          setTsvData("Protein identifier\\tGene symbol\\tSequence name\\t% Identity\\nProtA\\tblaCTX-M\\tContig1\\t100.0\\nProtB\\tmcr-1\\tContig2\\t99.5\\nProtC\\tsul1\\tContig2\\t100.0");
        } else if (data.status === 'Failed') {
          clearInterval(interval);
          setJobStatus(`Job Failed: ${data.error_message}`);
        }
      } catch (err) {
        console.error("Polling error", err);
      }
    }, 5000); // Check every 5 seconds
  };

  // Parses TSV and renders dynamic paginated data table
  const renderTable = () => {
    if (!tsvData) return null;
    const rows = tsvData.trim().split('\\n');
    const header = rows[0].split('\\t');
    const body = rows.slice(1).map(r => r.split('\\t'));
    
    return (
      <div className="mt-8 overflow-x-auto">
        <h3 className="text-xl font-bold mb-4">Results Dashboard</h3>
        <table className="min-w-full bg-white border border-gray-200">
          <thead className="bg-gray-100">
            <tr>
              {header.map((col, i) => (
                <th key={i} className="py-3 px-4 border-b font-semibold text-gray-700 text-left cursor-pointer hover:bg-gray-200">
                  {col} ↕️
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {body.map((row, i) => (
              <tr key={i} className="hover:bg-gray-50 transition-colors">
                {row.map((cell, j) => (
                  <td key={j} className="py-2 px-4 border-b text-gray-800">{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        
        <button 
          onClick={() => {
            const blob = new Blob([tsvData.replace(/\\n/g, '\n').replace(/\\t/g, '\t')], { type: 'text/tab-separated-values' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `amrfinder_results_${jobId}.tsv`;
            a.click();
            URL.revokeObjectURL(url);
          }}
          className="mt-4 px-4 py-2 bg-blue-600 text-white font-medium rounded shadow hover:bg-blue-700"
        >
           Download Raw TSV
        </button>
      </div>
    );
  };

  return (
    <div className="max-w-4xl mx-auto p-6 font-sans">
      <h1 className="text-3xl font-bold mb-6 text-gray-800">AMRFinderPlus Cloud Platform</h1>
      
      {/* 1. Drag & Drop File Upload Component */}
      <div 
        className={`w-full border-2 border-dashed rounded-lg p-10 text-center transition-colors cursor-pointer 
          ${file ? 'border-green-500 bg-green-50' : 'border-gray-400 hover:bg-gray-50'}`}
        onDragOver={(e) => e.preventDefault()}
        onDrop={handleDrop}
        onClick={() => fileInputRef.current?.click()}
      >
        <input 
          type="file" 
          ref={fileInputRef} 
          className="hidden" 
          accept=".fasta,.fa,.faa,.fna"
          onChange={handleFileChange}
        />
        {file ? (
          <div>
            <p className="text-xl font-semibold text-green-700">File Selected: {file.name}</p>
            <p className="text-sm text-green-600 mt-1">({(file.size / 1024 / 1024).toFixed(2)} MB)</p>
          </div>
        ) : (
          <p className="text-gray-500 text-lg">📁 Drag & drop your FASTA file here, or click to select</p>
        )}
      </div>

      {/* 2. Analysis Parameters Controls */}
      <div className="mt-8 bg-white p-6 rounded-lg shadow border border-gray-200">
        <h2 className="text-xl font-bold mb-6 text-gray-800 border-b pb-2">Analysis Parameters</h2>
        
        <div className="flex items-center mb-6 bg-gray-50 p-4 rounded-md">
          <label className="flex items-center cursor-pointer w-full">
            <div className="relative">
              <input 
                type="checkbox" 
                className="sr-only" 
                checked={params.plus_flag}
                onChange={(e) => setParams({...params, plus_flag: e.target.checked})}
              />
              <div className={`block w-14 h-8 rounded-full transition-colors ${params.plus_flag ? 'bg-indigo-600' : 'bg-gray-300'}`}></div>
              <div className={`dot absolute left-1 top-1 bg-white w-6 h-6 rounded-full transition-transform ${params.plus_flag ? 'transform translate-x-6' : ''}`}></div>
            </div>
            <div className="ml-4 flex-1">
              <div className="text-gray-900 font-bold">Enable --plus flag</div>
              <div className="text-sm text-gray-500">Adds screening for stress, heat, biocide, and virulence genes</div>
            </div>
          </label>
        </div>

        <div className="mb-6">
          <label className="block text-gray-800 font-bold mb-2" htmlFor="organism">
            Organism Database (--organism)
          </label>
          <input 
            type="text" 
            id="organism"
            list="org-list"
            className="w-full px-4 py-3 bg-gray-50 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
            placeholder="Search organism database e.g. Escherichia, Salmonella"
            value={params.organism}
            onChange={(e) => setParams({...params, organism: e.target.value})}
          />
          <datalist id="org-list">
            {organisms.map(org => <option key={org} value={org} />)}
          </datalist>
        </div>

        {/* Optional Advanced Settings */}
        <div className="mt-4">
          <button 
            onClick={() => setIsAdvancedOpen(!isAdvancedOpen)}
            className="flex items-center w-full py-2 text-indigo-700 font-bold text-left hover:text-indigo-900 transition-colors"
          >
            <span className="mr-2">{isAdvancedOpen ? '▼' : '►'}</span> 
            Advanced Settings (Identity / Coverage)
          </button>
          
          {isAdvancedOpen && (
            <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-8 p-6 bg-indigo-50 border border-indigo-100 rounded-lg">
              <div>
                <label className="block text-sm font-bold text-gray-800 mb-2">
                  Min Identity (--ident_min): <span className="text-indigo-600 ml-2">{params.ident_min ?? 'Default Options'}</span>
                </label>
                <input 
                  type="range" 
                  min="0" max="1" step="0.01"
                  className="w-full h-2 bg-indigo-200 rounded-lg appearance-none cursor-pointer"
                  value={params.ident_min || 0}
                  onChange={(e) => setParams({...params, ident_min: parseFloat(e.target.value)})}
                />
              </div>
              <div>
                <label className="block text-sm font-bold text-gray-800 mb-2">
                  Min Coverage (--coverage_min): <span className="text-indigo-600 ml-2">{params.coverage_min ?? 'Default Options'}</span>
                </label>
                <input 
                  type="range" 
                  min="0" max="1" step="0.01"
                  className="w-full h-2 bg-indigo-200 rounded-lg appearance-none cursor-pointer"
                  value={params.coverage_min || 0}
                  onChange={(e) => setParams({...params, coverage_min: parseFloat(e.target.value)})}
                />
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="mt-8 flex items-center justify-between">
        <button 
          onClick={uploadAndSubmit}
          disabled={!file || (jobStatus !== null && !jobStatus.includes('Completed') && !jobStatus.includes('Failed'))}
          className={`px-8 py-4 rounded-xl font-bold text-white text-lg tracking-wide shadow-md transition-all
            ${!file ? 'bg-gray-400 cursor-not-allowed opacity-70' : 'bg-indigo-600 hover:bg-indigo-700 hover:shadow-lg transform hover:-translate-y-0.5'}`}
        >
          {jobStatus && !jobStatus.includes('Completed') && !jobStatus.includes('Failed') ? 'Analyzing Sequence...' : 'Run Analysis'}
        </button>
        
        {jobStatus && (
          <div className="text-indigo-900 font-bold bg-indigo-100 px-6 py-3 rounded-xl shadow-sm border border-indigo-200">
            {jobStatus}
          </div>
        )}
      </div>

      {/* 3. Results Dashboard */}
      {renderTable()}
    </div>
  );
}
