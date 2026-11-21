export type PermissionSet = readonly string[]

export interface PermissionSubject {
  permissions?: PermissionSet | null
}

/**
 * Missing permission data is fail-closed in auth mode. The server remains the
 * authorization authority; these checks only shape frontend navigation/UI.
 */
export function hasPermission(
  subject: PermissionSubject | null | undefined,
  permission: string,
): boolean {
  return Boolean(
    permission &&
      Array.isArray(subject?.permissions) &&
      subject.permissions.includes(permission),
  )
}

export function hasAllPermissions(
  subject: PermissionSubject | null | undefined,
  permissions: readonly string[] = [],
): boolean {
  return permissions.every((permission) => hasPermission(subject, permission))
}

export function hasAnyPermission(
  subject: PermissionSubject | null | undefined,
  permissions: readonly string[] = [],
): boolean {
  return permissions.length === 0 || permissions.some((permission) =>
    hasPermission(subject, permission),
  )
}

export function canAccess(
  authEnabled: boolean,
  subject: PermissionSubject | null | undefined,
  permissions: readonly string[] = [],
): boolean {
  return !authEnabled || hasAllPermissions(subject, permissions)
}
