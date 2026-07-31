import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { UserInfo } from "../types/api";
import {
  changePassword,
  deleteAvatar,
  getAvatarUrl,
  updateProfile,
  uploadAvatar,
} from "../api/client";

interface ProfileSettingsProps {
  user: UserInfo;
  onUserUpdate: (user: UserInfo) => void;
  onPasswordChanged: (message: string) => void;
  onClose: () => void;
}

type Tab = "profile" | "password";

const ALLOWED_IMAGE_TYPES = ["image/jpeg", "image/png", "image/webp"];
const MAX_AVATAR_BYTES = 2 * 1024 * 1024;

export default function ProfileSettings({
  user,
  onUserUpdate,
  onPasswordChanged,
  onClose,
}: ProfileSettingsProps) {
  const [tab, setTab] = useState<Tab>("profile");

  // 个人资料编辑状态
  const [displayName, setDisplayName] = useState(user.display_name ?? "");
  const [email, setEmail] = useState(user.email ?? "");
  const [phone, setPhone] = useState(user.phone ?? "");
  const [bio, setBio] = useState(user.bio ?? "");
  const [profileSaving, setProfileSaving] = useState(false);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [profileSuccess, setProfileSuccess] = useState<string | null>(null);

  // 头像状态
  const [avatarPreview, setAvatarPreview] = useState<string | null>(null);
  const [avatarFile, setAvatarFile] = useState<File | null>(null);
  const [avatarUploading, setAvatarUploading] = useState(false);
  const [avatarError, setAvatarError] = useState<string | null>(null);

  // 修改密码状态
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmNewPassword, setConfirmNewPassword] = useState("");
  const [passwordSaving, setPasswordSaving] = useState(false);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordSuccess, setPasswordSuccess] = useState<string | null>(null);

  const avatarInputRef = useRef<HTMLInputElement>(null);
  const modalRef = useRef<HTMLDivElement>(null);

  /** 任一写操作进行中时为 busy：禁止关闭、切换页签 */
  const isBusy = useMemo(
    () => profileSaving || avatarUploading || passwordSaving,
    [profileSaving, avatarUploading, passwordSaving],
  );


  // 切换用户时同步编辑状态；同一用户上传头像时不重置正在编辑的资料
  useEffect(() => {
    setDisplayName(user.display_name ?? "");
    setEmail(user.email ?? "");
    setPhone(user.phone ?? "");
    setBio(user.bio ?? "");
    setAvatarPreview(null);
    setAvatarFile(null);
    setAvatarError(null);
  }, [user.user_id]);

  // Escape 关闭（busy 时禁止）
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape" && !isBusy) onClose();
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose, isBusy]);

  /** 遮罩点击关闭（busy 时禁止） */
  function handleOverlayClick(e: React.MouseEvent) {
    if (e.target === e.currentTarget && !isBusy) onClose();
  }

  /** 保存个人资料 */
  async function handleSaveProfile(e: FormEvent) {
    e.preventDefault();
    setProfileError(null);
    setProfileSuccess(null);
    setProfileSaving(true);

    try {
      let updated = await updateProfile({
        display_name: displayName.trim() || null,
        email: email.trim() || null,
        phone: phone.trim() || null,
        bio: bio.trim() || null,
      });

      // 用户选择了新头像时，由“保存资料”统一完成上传，避免只保存预览。
      if (avatarFile) {
        try {
          updated = await uploadAvatar(avatarFile);
        } catch (err) {
          onUserUpdate(updated);
          setProfileError(
            `资料已保存，但头像上传失败：${err instanceof Error ? err.message : "请重试"}`,
          );
          return;
        }

        if (avatarPreview) URL.revokeObjectURL(avatarPreview);
        setAvatarPreview(null);
        setAvatarFile(null);
        if (avatarInputRef.current) avatarInputRef.current.value = "";
      }

      onUserUpdate(updated);
      setProfileSuccess("资料保存成功。");
    } catch (err) {
      setProfileError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setProfileSaving(false);
    }
  }

  /** 选择头像 */
  function handleAvatarSelect(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;

    setAvatarError(null);

    if (!ALLOWED_IMAGE_TYPES.includes(file.type)) {
      setAvatarError("仅支持 JPEG、PNG、WebP 格式。");
      return;
    }
    if (file.size > MAX_AVATAR_BYTES) {
      setAvatarError("头像文件大小不能超过 2 MB。");
      return;
    }

    setAvatarFile(file);
    const previewUrl = URL.createObjectURL(file);
    setAvatarPreview(previewUrl);
  }

  /** 上传头像 */
  async function handleAvatarUpload() {
    if (!avatarFile) return;
    setAvatarError(null);
    setAvatarUploading(true);

    try {
      const updated = await uploadAvatar(avatarFile);
      onUserUpdate(updated);
      setAvatarFile(null);
      if (avatarPreview) URL.revokeObjectURL(avatarPreview);
      setAvatarPreview(null);
    } catch (err) {
      setAvatarError(err instanceof Error ? err.message : "头像上传失败");
    } finally {
      setAvatarUploading(false);
    }
  }

  /** 删除头像 */
  async function handleAvatarDelete() {
    setAvatarError(null);
    setAvatarUploading(true);

    try {
      const updated = await deleteAvatar();
      onUserUpdate(updated);
      setAvatarFile(null);
      if (avatarPreview) URL.revokeObjectURL(avatarPreview);
      setAvatarPreview(null);
    } catch (err) {
      setAvatarError(err instanceof Error ? err.message : "头像删除失败");
    } finally {
      setAvatarUploading(false);
    }
  }

  /** 取消头像选择 */
  function handleAvatarCancel() {
    setAvatarFile(null);
    if (avatarPreview) URL.revokeObjectURL(avatarPreview);
    setAvatarPreview(null);
    if (avatarInputRef.current) avatarInputRef.current.value = "";
  }

  /** 修改密码 */
  async function handleChangePassword(e: FormEvent) {
    e.preventDefault();
    setPasswordError(null);
    setPasswordSuccess(null);

    if (newPassword.length < 8 || newPassword.length > 256) {
      setPasswordError("新密码长度需在 8～256 位之间。");
      return;
    }
    if (newPassword === currentPassword) {
      setPasswordError("新密码不能与当前密码相同。");
      return;
    }
    if (newPassword !== confirmNewPassword) {
      setPasswordError("两次输入的新密码不一致。");
      return;
    }

    setPasswordSaving(true);

    try {
      await changePassword({
        current_password: currentPassword,
        new_password: newPassword,
      });
      // 改密成功后立即通知父组件执行完整清理，不依赖可被卸载取消的延迟
      onPasswordChanged("密码修改成功，请使用新密码登录。");
      // 函数返回后组件将被父级卸载，此处不加 setState
    } catch (err) {
      setPasswordError(err instanceof Error ? err.message : "密码修改失败");
    } finally {
      setPasswordSaving(false);
    }
  }

  // 清理预览 blob
  const cleanupPreview = useCallback(() => {
    if (avatarPreview) URL.revokeObjectURL(avatarPreview);
  }, [avatarPreview]);

  useEffect(() => {
    return cleanupPreview;
  }, [cleanupPreview]);

  const avatarSrc = avatarPreview ?? getAvatarUrl(user.avatar_url);
  const displayLabel = user.display_name || user.username;
  const avatarPlaceholder = displayLabel.charAt(0).toUpperCase();

  /** 安全关闭：busy 时忽略 */
  function safeClose() {
    if (!isBusy) onClose();
  }

  return (
    <div className="modal-overlay" onClick={handleOverlayClick} ref={modalRef}>
      <div className="modal-container">
        <div className="modal-header">
          <h2 className="modal-title">个人设置</h2>
          <button
            type="button"
            className="modal-close-button"
            onClick={safeClose}
            disabled={isBusy}
            aria-label="关闭"
          >
            ✕
          </button>
        </div>

        <div className="modal-tabs">
          <button
            type="button"
            className={`modal-tab ${tab === "profile" ? "active" : ""}`}
            onClick={() => !isBusy && setTab("profile")}
            disabled={isBusy}
          >
            个人资料
          </button>
          <button
            type="button"
            className={`modal-tab ${tab === "password" ? "active" : ""}`}
            onClick={() => !isBusy && setTab("password")}
            disabled={isBusy}
          >
            修改密码
          </button>
        </div>

        {/* 个人资料 */}
        {tab === "profile" && (
          <div className="modal-body">
            <div className="profile-avatar-section">
              <div className="profile-avatar-preview">
                {avatarSrc ? (
                  <img
                    src={avatarSrc}
                    alt="头像"
                    className="profile-avatar-img"
                  />
                ) : (
                  <span className="profile-avatar-placeholder">
                    {avatarPlaceholder}
                  </span>
                )}
              </div>
              <div className="profile-avatar-actions">
                <input
                  ref={avatarInputRef}
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  className="avatar-input-hidden"
                  onChange={handleAvatarSelect}
                />
                <button
                  type="button"
                  className="profile-button-secondary"
                  onClick={() => avatarInputRef.current?.click()}
                  disabled={avatarUploading || profileSaving}
                >
                  选择图片
                </button>
                {avatarFile && (
                  <>
                    <button
                      type="button"
                      className="profile-button-primary"
                      onClick={handleAvatarUpload}
                      disabled={avatarUploading || profileSaving}
                    >
                      {avatarUploading ? "上传中..." : "上传"}
                    </button>
                    <button
                      type="button"
                      className="profile-button-secondary"
                      onClick={handleAvatarCancel}
                      disabled={avatarUploading || profileSaving}
                    >
                      取消
                    </button>
                  </>
                )}
                {user.avatar_url && !avatarFile && (
                  <button
                    type="button"
                    className="profile-button-danger"
                    onClick={handleAvatarDelete}
                    disabled={avatarUploading || profileSaving}
                  >
                    {avatarUploading ? "删除中..." : "删除头像"}
                  </button>
                )}
              </div>
              {avatarError && (
                <span className="profile-inline-error">{avatarError}</span>
              )}
            </div>

            <form className="profile-form" onSubmit={handleSaveProfile}>
              <div className="profile-field">
                <label>用户名</label>
                <input
                  type="text"
                  value={user.username}
                  disabled
                  className="profile-input-readonly"
                  title="用户名是固定的登录账号，不可修改"
                />
              </div>

              <div className="profile-field">
                <label htmlFor="profile-display-name">昵称</label>
                <input
                  id="profile-display-name"
                  type="text"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  disabled={profileSaving}
                  maxLength={64}
                  placeholder="填写昵称（选填）"
                />
              </div>

              <div className="profile-field">
                <label htmlFor="profile-email">邮箱</label>
                <input
                  id="profile-email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  disabled={profileSaving}
                  maxLength={254}
                  placeholder="填写邮箱（选填）"
                />
              </div>

              <div className="profile-field">
                <label htmlFor="profile-phone">手机号</label>
                <input
                  id="profile-phone"
                  type="text"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  disabled={profileSaving}
                  maxLength={32}
                  placeholder="填写手机号（选填）"
                />
              </div>

              <div className="profile-field">
                <label htmlFor="profile-bio">个人简介</label>
                <textarea
                  id="profile-bio"
                  value={bio}
                  onChange={(e) => setBio(e.target.value)}
                  disabled={profileSaving}
                  maxLength={500}
                  rows={3}
                  placeholder="简单介绍一下自己（选填，最多 500 字）"
                />
                <span className="profile-char-count">
                  {bio.length}/500
                </span>
              </div>

              {profileError && (
                <div className="profile-error">❌ {profileError}</div>
              )}
              {profileSuccess && (
                <div className="profile-success">✅ {profileSuccess}</div>
              )}

              <button
                type="submit"
                className="profile-button-primary profile-save-button"
                disabled={profileSaving || avatarUploading}
              >
                {profileSaving ? "保存中..." : "保存资料"}
              </button>
            </form>
          </div>
        )}

        {/* 修改密码 */}
        {tab === "password" && (
          <div className="modal-body">
            <form className="profile-form" onSubmit={handleChangePassword}>
              <div className="profile-field">
                <label htmlFor="password-current">当前密码</label>
                <input
                  id="password-current"
                  type="password"
                  autoComplete="current-password"
                  value={currentPassword}
                  onChange={(e) => setCurrentPassword(e.target.value)}
                  disabled={passwordSaving}
                  maxLength={256}
                  placeholder="请输入当前密码"
                />
              </div>

              <div className="profile-field">
                <label htmlFor="password-new">新密码</label>
                <input
                  id="password-new"
                  type="password"
                  autoComplete="new-password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  disabled={passwordSaving}
                  maxLength={256}
                  placeholder="8～256 位新密码"
                />
              </div>

              <div className="profile-field">
                <label htmlFor="password-confirm">确认新密码</label>
                <input
                  id="password-confirm"
                  type="password"
                  autoComplete="new-password"
                  value={confirmNewPassword}
                  onChange={(e) => setConfirmNewPassword(e.target.value)}
                  disabled={passwordSaving}
                  maxLength={256}
                  placeholder="再次输入新密码"
                />
              </div>

              {passwordError && (
                <div className="profile-error">❌ {passwordError}</div>
              )}
              {passwordSuccess && (
                <div className="profile-success">✅ {passwordSuccess}</div>
              )}

              <button
                type="submit"
                className="profile-button-primary profile-save-button"
                disabled={
                  passwordSaving ||
                  !currentPassword ||
                  !newPassword ||
                  !confirmNewPassword
                }
              >
                {passwordSaving ? "修改中..." : "修改密码"}
              </button>
            </form>
          </div>
        )}
      </div>
    </div>
  );
}
