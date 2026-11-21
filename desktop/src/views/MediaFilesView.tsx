import React, { useCallback, useEffect, useState } from 'react';
import { ArrowRightLeft, Download, Folder, FileText, Music, RefreshCw, Upload } from 'lucide-react';
import { apiRequest, fetchAuthorizedBlob } from '../api/client';
import type { ActiveMediaSession, Device, FileTransfer, MediaItem } from '../types/models';

interface Props {
  devices: Device[];
  onNotify: (msg: string) => void;
}

interface DirEntry {
  name: string;
  path: string;
  is_dir: boolean;
  size: number;
}

export const MediaFilesView: React.FC<Props> = ({ devices, onNotify }) => {
  const enrolledDevices = devices.filter((d) => d.is_registered && d.connection_status === 'online');
  const [selectedDeviceId, setSelectedDeviceId] = useState<string>(enrolledDevices[0]?.id || '');
  const [currentPath, setCurrentPath] = useState('');
  const [entries, setEntries] = useState<DirEntry[]>([]);
  const [transfers, setTransfers] = useState<FileTransfer[]>([]);
  const [mediaSessions, setMediaSessions] = useState<ActiveMediaSession[]>([]);
  const [mediaItems, setMediaItems] = useState<MediaItem[]>([]);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [playingTitle, setPlayingTitle] = useState<string | null>(null);

  // Device-to-Device transfer modal state
  const [d2dSourcePath, setD2dSourcePath] = useState<string | null>(null);
  const [d2dTargetDevice, setD2dTargetDevice] = useState<string>('');
  const [d2dTargetDir, setD2dTargetDir] = useState<string>('');

  useEffect(() => {
    if (!selectedDeviceId && enrolledDevices.length > 0) {
      setSelectedDeviceId(enrolledDevices[0].id);
    }
  }, [enrolledDevices, selectedDeviceId]);

  const loadAuxData = useCallback(async () => {
    try {
      const [trRes, msRes, miRes] = await Promise.all([
        apiRequest<{ transfers: FileTransfer[] }>('/files/transfers'),
        apiRequest<{ sessions: ActiveMediaSession[] }>('/media/sessions'),
        apiRequest<{ items: MediaItem[] }>('/media/items'),
      ]);
      setTransfers(trRes.transfers || []);
      setMediaSessions(msRes.sessions || []);
      setMediaItems(miRes.items || []);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Failed to load media & transfers');
    }
  }, [onNotify]);

  const browseDirectory = useCallback(
    async (devId: string, path: string) => {
      if (!devId) return;
      try {
        const res = await apiRequest<{ current_path: string; entries: DirEntry[] }>(
          `/files/browse/${devId}?path=${encodeURIComponent(path)}`
        );
        setCurrentPath(res.current_path || path);
        setEntries(res.entries || []);
      } catch (err) {
        setEntries([]);
        onNotify(err instanceof Error ? err.message : 'File browse failed');
      }
    },
    [onNotify]
  );

  useEffect(() => {
    void loadAuxData();
  }, [loadAuxData]);

  useEffect(() => {
    if (selectedDeviceId) {
      void browseDirectory(selectedDeviceId, '');
    }
  }, [selectedDeviceId, browseDirectory]);

  const handleDownload = async (filePath: string, fileName: string) => {
    try {
      const { blob, sha256 } = await fetchAuthorizedBlob(
        `/files/download/${selectedDeviceId}?path=${encodeURIComponent(filePath)}`
      );
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = fileName;
      a.click();
      URL.revokeObjectURL(url);
      onNotify(`Downloaded '${fileName}' (SHA-256: ${sha256?.slice(0, 12)}…).`);
      await loadAuxData();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Download failed');
    }
  };

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !selectedDeviceId) return;
    const form = new FormData();
    form.append('file', file);
    form.append('target_dir', currentPath);
    try {
      await apiRequest(`/files/upload/${selectedDeviceId}`, {
        method: 'POST',
        body: form,
      });
      onNotify(`Uploaded '${file.name}' with SHA-256 verification.`);
      await browseDirectory(selectedDeviceId, currentPath);
      await loadAuxData();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Upload failed');
    }
  };

  const executeD2DTransfer = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!d2dSourcePath || !d2dTargetDevice) return;
    try {
      const res = await apiRequest<{ transfer: FileTransfer }>('/files/transfer', {
        method: 'POST',
        body: JSON.stringify({
          source_device_id: selectedDeviceId,
          source_path: d2dSourcePath,
          target_device_id: d2dTargetDevice,
          target_dir: d2dTargetDir,
        }),
      });
      onNotify(`Device-to-device transfer ${res.transfer.status} (SHA-256 verified).`);
      setD2dSourcePath(null);
      await loadAuxData();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Device-to-device transfer failed');
    }
  };

  const indexMediaOnDevice = async () => {
    if (!selectedDeviceId) return;
    try {
      const res = await apiRequest<{ indexed_count: number }>(`/media/index/${selectedDeviceId}`, {
        method: 'POST',
      });
      onNotify(`Indexed ${res.indexed_count} authorized media files.`);
      await loadAuxData();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Media indexing failed');
    }
  };

  const playMediaItem = async (item: MediaItem) => {
    try {
      const { blob } = await fetchAuthorizedBlob(`/media/stream/${item.id}`);
      if (audioUrl) URL.revokeObjectURL(audioUrl);
      const nextUrl = URL.createObjectURL(blob);
      setAudioUrl(nextUrl);
      setPlayingTitle(item.title);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Media playback stream failed');
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div className="section-header">
        <div>
          <h2 style={{ fontSize: '19px', fontWeight: 700 }}>Authorized Media & File Management</h2>
          <p style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>
            Root-jailed file browsing, SHA-256 verified uploads/downloads, device-to-device relay, and remote media playback.
          </p>
        </div>

        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <select
            className="select"
            value={selectedDeviceId}
            onChange={(e) => setSelectedDeviceId(e.target.value)}
          >
            <option value="">Select Enrolled Online Device...</option>
            {enrolledDevices.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name} ({d.adapter_type})
              </option>
            ))}
          </select>
          <button className="btn" onClick={() => browseDirectory(selectedDeviceId, '')} disabled={!selectedDeviceId}>
            <RefreshCw size={14} /> Refresh Root
          </button>
          <button className="btn" onClick={indexMediaOnDevice} disabled={!selectedDeviceId}>
            <Music size={14} /> Index Media
          </button>
          <label className="btn btn-primary" style={{ cursor: selectedDeviceId ? 'pointer' : 'not-allowed' }}>
            <Upload size={14} /> Upload File
            <input type="file" style={{ display: 'none' }} onChange={handleUpload} disabled={!selectedDeviceId} />
          </label>
        </div>
      </div>

      {/* Authorized File Browser */}
      <div className="card">
        <div className="section-header">
          <div>
            <div className="section-title">Authorized Remote Directory Browser</div>
            <div className="mono" style={{ color: 'var(--text-muted)', marginTop: '2px' }}>
              Path: {currentPath || '(Select an enrolled online device with files:browse permission)'}
            </div>
          </div>
        </div>

        {entries.length === 0 ? (
          <div className="empty-state">
            No files listed or no online device selected. Ensure <span className="mono">files:browse</span> permission is granted.
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Type</th>
                <th>Size</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.path}>
                  <td style={{ display: 'flex', alignItems: 'center', gap: '8px', fontWeight: 500 }}>
                    {entry.is_dir ? <Folder size={15} color="var(--accent-primary)" /> : <FileText size={15} color="var(--text-secondary)" />}
                    {entry.name}
                  </td>
                  <td>{entry.is_dir ? 'Directory' : 'File'}</td>
                  <td className="tabular">{entry.is_dir ? '—' : `${entry.size.toLocaleString()} B`}</td>
                  <td style={{ display: 'flex', gap: '8px' }}>
                    {entry.is_dir ? (
                      <button className="btn btn-sm" onClick={() => browseDirectory(selectedDeviceId, entry.path)}>
                        Open
                      </button>
                    ) : (
                      <>
                        <button className="btn btn-sm" onClick={() => handleDownload(entry.path, entry.name)}>
                          <Download size={13} /> Download
                        </button>
                        <button
                          className="btn btn-sm btn-primary"
                          onClick={() => {
                            setD2dSourcePath(entry.path);
                            setD2dTargetDevice(enrolledDevices.find((d) => d.id !== selectedDeviceId)?.id || selectedDeviceId);
                          }}
                        >
                          <ArrowRightLeft size={13} /> Send to Device
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Media Library & Audio Player + Active Playback Devices */}
      <div className="grid-2">
        <div className="card">
          <div className="section-title" style={{ marginBottom: '10px' }}>Authorized Media Catalog</div>
          {audioUrl && (
            <div style={{ marginBottom: '12px', padding: '10px', background: 'var(--bg-canvas)', borderRadius: '8px' }}>
              <div style={{ fontSize: '12px', marginBottom: '6px' }}>
                Streaming: <strong>{playingTitle}</strong>
              </div>
              <audio controls autoPlay src={audioUrl} style={{ width: '100%', height: '34px' }} />
            </div>
          )}
          {mediaItems.length === 0 ? (
            <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>
              Click &quot;Index Media&quot; to discover photos, videos, and audio inside authorized folders.
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {mediaItems.map((m) => (
                <div
                  key={m.id}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    padding: '8px 10px',
                    background: 'var(--bg-canvas)',
                    borderRadius: '8px',
                  }}
                >
                  <div>
                    <div style={{ fontWeight: 600 }}>{m.title}</div>
                    <div style={{ fontSize: '11.5px', color: 'var(--text-muted)' }}>
                      {m.media_type} · {m.mime_type} · {m.size_bytes.toLocaleString()} B
                    </div>
                  </div>
                  {m.media_type === 'audio' && (
                    <button className="btn btn-sm btn-primary" onClick={() => playMediaItem(m)}>
                      Play Stream
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="card">
          <div className="section-title" style={{ marginBottom: '10px' }}>Transfer Ledger & Playback Devices</div>
          {mediaSessions.length > 0 && (
            <div style={{ marginBottom: '12px' }}>
              {mediaSessions.map((ms) => (
                <div key={ms.id} style={{ padding: '8px 10px', background: 'var(--bg-canvas)', borderRadius: '8px', marginBottom: '6px' }}>
                  <strong>{ms.device_name}</strong>: {ms.title} ({ms.playback_state})
                </div>
              ))}
            </div>
          )}
          {transfers.length === 0 ? (
            <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>No file transfers executed yet.</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxHeight: '240px', overflowY: 'auto' }}>
              {transfers.map((t) => (
                <div key={t.id} style={{ padding: '8px 10px', background: 'var(--bg-canvas)', borderRadius: '6px', fontSize: '12px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <strong>
                      [{t.direction}] {t.file_name}
                    </strong>
                    <span className={`badge ${t.status === 'completed' ? 'badge-online' : 'badge-isolated'}`}>
                      {t.status}
                    </span>
                  </div>
                  {t.sha256_checksum && (
                    <div className="mono" style={{ color: 'var(--text-muted)', fontSize: '11px', marginTop: '2px' }}>
                      SHA-256: {t.sha256_checksum.slice(0, 24)}… ({t.size_bytes} B)
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Device-to-Device Transfer Modal */}
      {d2dSourcePath && (
        <div className="modal-backdrop" onClick={() => setD2dSourcePath(null)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <h3 style={{ fontSize: '16px', fontWeight: 700, marginBottom: '10px' }}>
              Device-to-Device Verified File Transfer
            </h3>
            <form onSubmit={executeD2DTransfer} style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <div className="mono" style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                Source File: {d2dSourcePath}
              </div>
              <div>
                <label style={{ display: 'block', fontSize: '12px', marginBottom: '4px' }}>Destination Enrolled Device</label>
                <select className="select" style={{ width: '100%' }} value={d2dTargetDevice} onChange={(e) => setD2dTargetDevice(e.target.value)} required>
                  {enrolledDevices.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name} ({d.adapter_type})
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label style={{ display: 'block', fontSize: '12px', marginBottom: '4px' }}>Destination Subdirectory (optional)</label>
                <input className="input" style={{ width: '100%' }} value={d2dTargetDir} onChange={(e) => setD2dTargetDir(e.target.value)} placeholder="Leave empty for shared root" />
              </div>
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
                <button type="button" className="btn" onClick={() => setD2dSourcePath(null)}>Cancel</button>
                <button type="submit" className="btn btn-primary">Transfer & Verify SHA-256</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
